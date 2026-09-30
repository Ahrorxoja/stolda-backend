"""Agentlar: biriktirish, daromad, pul yechish va botlar."""

import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from agents import bot as agent_bot
from agents import services
from agents.models import Agent, AgentEarning, AgentWithdrawal
from menu.billing_ledger import approve_receipt
from menu.management.commands.telegram_bot import Command as OwnerBotCommand
from menu.models import Invoice, PaymentReceipt, Restaurant, Subscription
from menu.tests.factories import make_plan, make_restaurant
from telegrambot import FakeBot, FakeChatBot

ADMIN_CHAT_ID = "4242"
AGENT_CHAT_ID = "777"


def receipt_image() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "white").save(buffer, format="PNG")
    return SimpleUploadedFile("chek.png", buffer.getvalue(), content_type="image/png")


def make_agent(**kwargs) -> Agent:
    defaults = {"name": "Ali Valiyev", "code": "ALI25", "phone": "+998901112233"}
    return Agent.objects.create(**{**defaults, **kwargs})


class AgentTestCase(TestCase):
    """Soxta botlar: agentga va platforma egasiga ketgan xabarlarni ushlaydi."""

    def setUp(self):
        self.agent_bot = FakeChatBot()
        self.owner_bot = FakeBot()
        patches = [
            patch("agents.services.get_agent_bot", return_value=self.agent_bot),
            patch("agents.services.get_bot", return_value=self.owner_bot),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def pay(self, restaurant: Restaurant, period="month", when=None) -> Invoice:
        """Chek yuklanib, tasdiqlangandek — `on_commit` xabarlari bilan."""
        subscription = restaurant.subscription
        amount = subscription.plan.price_year if period == "year" else subscription.plan.price_month
        receipt = PaymentReceipt.objects.create(
            subscription=subscription, image=receipt_image(), amount=amount, period=period
        )
        with self.captureOnCommitCallbacks(execute=True):
            if when:
                with patch("django.utils.timezone.now", return_value=when):
                    return approve_receipt(receipt)
            return approve_receipt(receipt)


# ── Ro'yxatdan o'tishda biriktirish ───────────────────────────────────


class SignupAttachTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        make_plan()
        self.agent = make_agent(telegram_chat_id=AGENT_CHAT_ID)
        self.user = get_user_model().objects.create_user(username="ega@gmail.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create(self, **extra):
        body = {"name": "Zamin", "slug": "zamin", "phone": "+998 90 123 45 67", **extra}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post("/api/restaurants/", body, format="json")

    def test_code_attaches_agent_and_gives_longer_trial(self):
        response = self.create(agent_code=" ali25 ")

        self.assertEqual(response.status_code, 201, response.data)
        restaurant = Restaurant.objects.get()
        self.assertEqual(restaurant.agent, self.agent)
        trial = restaurant.subscription.trial_ends_at - restaurant.trial_used_at
        self.assertEqual(trial.days, services.AGENT_TRIAL_DAYS)
        self.assertIn("Zamin", self.agent_bot.texts(AGENT_CHAT_ID)[0])

    def test_without_code_nothing_changes(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.data)
        restaurant = Restaurant.objects.get()
        self.assertIsNone(restaurant.agent)
        self.assertEqual((restaurant.subscription.trial_ends_at - restaurant.trial_used_at).days, 14)

    def test_wrong_code_is_rejected_and_nothing_is_created(self):
        response = self.create(agent_code="NOPE")

        self.assertEqual(response.status_code, 400)
        self.assertIn("agent_code", response.data)
        self.assertFalse(Restaurant.objects.exists())

    def test_inactive_agent_code_is_rejected(self):
        self.agent.is_active = False
        self.agent.save()

        self.assertEqual(self.create(agent_code="ALI25").status_code, 400)

    def test_code_can_be_added_later_during_trial_only(self):
        self.create()
        restaurant = Restaurant.objects.get()

        response = self.client.patch(f"/api/restaurants/{restaurant.pk}/", {"agent_code": "ALI25"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        restaurant.refresh_from_db()
        self.assertEqual(restaurant.agent, self.agent)

        # To'lagandan keyin — boshqa agentga o'tkazib bo'lmaydi.
        other = make_agent(name="Jasur", code="JASUR")
        Subscription.objects.filter(restaurant=restaurant).update(status="active")
        self.client.patch(f"/api/restaurants/{restaurant.pk}/", {"agent_code": other.code}, format="json")
        restaurant.refresh_from_db()
        self.assertEqual(restaurant.agent, self.agent)

    def test_check_endpoint(self):
        ok = self.client.get("/api/agents/check/?code=ali25").json()
        self.assertEqual(ok, {"valid": True, "name": "Ali", "code": "ALI25"})
        self.assertEqual(self.client.get("/api/agents/check/?code=zzz").json()["valid"], False)
        self.assertEqual(APIClient().get("/api/agents/check/?code=ALI25").status_code, 401)


# ── Daromad ───────────────────────────────────────────────────────────


class EarningTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.agent = make_agent(telegram_chat_id=AGENT_CHAT_ID)
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.TRIALING)
        services.attach(self.restaurant, self.agent)

    def amounts(self):
        return sorted(AgentEarning.objects.values_list("kind", "amount"))

    def test_first_monthly_payment_gives_bonus_and_percent(self):
        self.pay(self.restaurant)

        self.assertEqual(self.amounts(), [("bonus", 30_000), ("percent", 19_800)])
        self.assertIn("+49 800 so'm", self.agent_bot.texts(AGENT_CHAT_ID)[-1])

    def test_second_payment_gives_only_percent(self):
        self.pay(self.restaurant)
        self.pay(self.restaurant)

        self.assertEqual(
            sorted(AgentEarning.objects.values_list("kind", flat=True)), ["bonus", "percent", "percent"]
        )

    def test_yearly_payment(self):
        self.pay(self.restaurant, period="year")

        self.assertEqual(self.amounts(), [("bonus", 30_000), ("percent", 198_000)])

    def test_approving_twice_does_not_double(self):
        invoice = self.pay(self.restaurant)
        services.on_invoice_paid(invoice)

        self.assertEqual(AgentEarning.objects.count(), 2)

    def test_nothing_after_the_agreed_months(self):
        start = timezone.now()
        self.pay(self.restaurant, when=start)
        self.pay(self.restaurant, when=start + timedelta(days=12 * 30 + 5))

        self.assertEqual(AgentEarning.objects.filter(kind="percent").count(), 1)

    def test_inactive_agent_earns_nothing(self):
        self.agent.is_active = False
        self.agent.save()

        self.pay(self.restaurant)

        self.assertFalse(AgentEarning.objects.exists())

    def test_restaurant_without_agent_earns_nothing(self):
        other = make_restaurant(slug="boshqa", subscription_status=Subscription.Status.TRIALING)

        self.pay(other)

        self.assertFalse(AgentEarning.objects.exists())

    def test_custom_terms_per_agent(self):
        self.agent.percent, self.agent.first_bonus = 30, 0
        self.agent.save()

        self.pay(self.restaurant)

        self.assertEqual(self.amounts(), [("percent", 29_700)])


# ── Pul yechish ───────────────────────────────────────────────────────


@override_settings(TELEGRAM_ADMIN_CHAT_ID=ADMIN_CHAT_ID)
class WithdrawalTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.agent = make_agent(
            telegram_chat_id=AGENT_CHAT_ID, card_number="8600 1234 5678 9012", card_holder="ALI VALIYEV"
        )
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.TRIALING)
        services.attach(self.restaurant, self.agent)

    def fund(self, times=3):
        for _ in range(times):
            self.pay(self.restaurant)

    def request(self) -> AgentWithdrawal:
        with self.captureOnCommitCallbacks(execute=True):
            return services.request_withdrawal(self.agent)

    def test_request_goes_to_owner_with_full_card_and_buttons(self):
        self.fund()  # 30 000 + 3 × 19 800 = 89 400 — hali kam
        with self.assertRaises(services.WithdrawalError):
            self.request()
        self.pay(self.restaurant)  # 109 200

        withdrawal = self.request()

        self.assertEqual(withdrawal.amount, 109_200)
        self.assertEqual(services.balance(self.agent).available, 0)
        self.assertEqual(services.balance(self.agent).pending, 109_200)
        message = self.owner_bot.sent[-1]
        self.assertIn("8600 1234 5678 9012", message["text"])
        self.assertEqual(
            message["buttons"], [("✅ O'tkazdim", f"wd_paid:{withdrawal.pk}"), ("❌ Rad etish", f"wd_reject:{withdrawal.pk}")]
        )

    def test_card_is_required(self):
        self.agent.card_number = ""
        self.agent.save()
        self.fund(5)

        with self.assertRaisesMessage(services.WithdrawalError, "kartangizni"):
            self.request()

    def test_only_one_pending_request(self):
        self.fund(5)
        self.request()
        self.pay(self.restaurant)

        with self.assertRaisesMessage(services.WithdrawalError, "ko'rib chiqilmoqda"):
            self.request()

    def press(self, action: str, withdrawal: AgentWithdrawal, chat_id=ADMIN_CHAT_ID) -> FakeBot:
        bot = FakeBot()
        with self.captureOnCommitCallbacks(execute=True):
            OwnerBotCommand()._handle_callback(
                bot,
                {
                    "id": "cb",
                    "data": f"{action}:{withdrawal.pk}",
                    "message": {"message_id": "9", "chat": {"id": chat_id}, "text": "so'rov"},
                },
            )
        return bot

    def test_owner_confirms_transfer(self):
        self.fund(5)
        withdrawal = self.request()

        bot = self.press("wd_paid", withdrawal)

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, AgentWithdrawal.Status.PAID)
        self.assertEqual(services.balance(self.agent).paid, withdrawal.amount)
        self.assertIn("O'tkazildi", bot.edited[0]["text"])
        self.assertIn("kartangizga o'tkazildi", self.agent_bot.texts(AGENT_CHAT_ID)[-1])
        # Qayta bosish — hech narsa o'zgarmaydi.
        self.assertIn("allaqachon", self.press("wd_paid", withdrawal).answered[0]["text"])

    def test_owner_rejects_and_money_returns_to_balance(self):
        self.fund(5)
        withdrawal = self.request()

        self.press("wd_reject", withdrawal)

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, AgentWithdrawal.Status.REJECTED)
        self.assertEqual(services.balance(self.agent).available, withdrawal.amount)
        self.assertIn("balansingizga qaytdi", self.agent_bot.texts(AGENT_CHAT_ID)[-1])

    def test_stranger_cannot_press_the_buttons(self):
        self.fund(5)
        withdrawal = self.request()

        self.press("wd_paid", withdrawal, chat_id="1")

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, AgentWithdrawal.Status.PENDING)


# ── Agentlar boti ─────────────────────────────────────────────────────


class AgentBotTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.agent = make_agent()
        self.bot = FakeChatBot()

    def say(self, text: str, chat_id=AGENT_CHAT_ID, **message):
        agent_bot.handle_message(
            self.bot,
            {"chat": {"id": chat_id, "type": "private"}, "from": {"username": "ali"}, "text": text, **message},
        )
        self.agent.refresh_from_db()
        return self.bot.sent[-1] if self.bot.sent else None

    def test_start_with_invite_links_the_agent(self):
        reply = self.say(f"/start {self.agent.invite_token}")

        self.assertEqual(self.agent.telegram_chat_id, AGENT_CHAT_ID)
        self.assertEqual(self.agent.telegram_username, "ali")
        self.assertIn("ALI25", reply["text"])
        self.assertEqual(reply["keyboard"], agent_bot.KEYBOARD)

    def test_strangers_are_turned_away(self):
        self.assertIn("agentlari uchun", self.say("/start", chat_id="1")["text"])
        self.assertIn("agentlari uchun", self.say(agent_bot.BTN_BALANCE, chat_id="1")["text"])
        self.assertIn("agentlari uchun", self.say("/start boshqa-token", chat_id="1")["text"])

    def test_invite_cannot_be_reused_by_another_account(self):
        self.say(f"/start {self.agent.invite_token}")

        reply = self.say(f"/start {self.agent.invite_token}", chat_id="999")

        self.assertIn("boshqa Telegram", reply["text"])
        self.assertEqual(self.agent.telegram_chat_id, AGENT_CHAT_ID)

    def test_link_button_sends_qr_with_personal_link(self):
        self.say(f"/start {self.agent.invite_token}")

        reply = self.say(agent_bot.BTN_LINK)

        self.assertTrue(reply["photo"].startswith(b"\x89PNG"))
        self.assertIn("?agent=ALI25", reply["caption"])

    def test_balance_and_restaurants(self):
        self.say(f"/start {self.agent.invite_token}")
        restaurant = make_restaurant(subscription_status=Subscription.Status.TRIALING)
        services.attach(restaurant, self.agent)
        self.pay(restaurant)

        self.assertIn("49 800 so'm", self.say(agent_bot.BTN_BALANCE)["text"])
        restaurants = self.say(agent_bot.BTN_RESTAURANTS)["text"]
        self.assertIn("Zamin", restaurants)
        self.assertIn("to'layapti", restaurants)

    def test_card_flow(self):
        self.say(f"/start {self.agent.invite_token}")

        self.say(agent_bot.BTN_CARD)
        self.assertIn("16 ta raqam", self.say("1234")["text"])
        self.say("8600-1234-5678-9012")
        reply = self.say("ali   valiyev")

        self.assertEqual(self.agent.card_number, "8600 1234 5678 9012")
        self.assertEqual(self.agent.card_holder, "ALI VALIYEV")
        self.assertEqual(self.agent.bot_state, "")
        self.assertIn("•••• 9012", reply["text"])

    def test_pressing_a_button_cancels_card_input(self):
        self.say(f"/start {self.agent.invite_token}")
        self.say(agent_bot.BTN_CARD)

        self.say(agent_bot.BTN_BALANCE)

        self.assertEqual(self.agent.bot_state, "")
        self.assertEqual(self.agent.card_number, "")

    def test_withdraw_button_explains_what_is_missing(self):
        self.say(f"/start {self.agent.invite_token}")

        self.assertIn("kartangizni", self.say(agent_bot.BTN_WITHDRAW)["text"])

    def test_inactive_agent_can_still_see_balance_but_not_link(self):
        self.say(f"/start {self.agent.invite_token}")
        Agent.objects.filter(pk=self.agent.pk).update(is_active=False)

        self.assertIn("faol emas", self.say(agent_bot.BTN_LINK)["text"])
        self.assertIn("Balans", self.say(agent_bot.BTN_BALANCE)["text"])

    def test_group_chats_are_ignored(self):
        agent_bot.handle_message(self.bot, {"chat": {"id": "-100", "type": "group"}, "text": "/start"})

        self.assertEqual(self.bot.sent, [])
