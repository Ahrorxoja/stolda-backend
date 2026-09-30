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

    def test_first_monthly_payment_gives_half(self):
        self.pay(self.restaurant)

        self.assertEqual(self.amounts(), [("first", 49_500)])
        self.assertIn("+49 500 so'm", self.agent_bot.texts(AGENT_CHAT_ID)[-1])

    def test_next_payments_give_twenty_percent(self):
        self.pay(self.restaurant)
        self.pay(self.restaurant)
        self.pay(self.restaurant)

        self.assertEqual(self.amounts(), [("first", 49_500), ("percent", 19_800), ("percent", 19_800)])

    def test_first_yearly_payment_also_gives_half(self):
        self.pay(self.restaurant, period="year")

        self.assertEqual(self.amounts(), [("first", 495_000)])

    def test_approving_twice_does_not_double(self):
        invoice = self.pay(self.restaurant)
        services.on_invoice_paid(invoice)

        self.assertEqual(AgentEarning.objects.count(), 1)

    def test_next_payments_only_within_the_agreed_months(self):
        start = timezone.now()
        self.pay(self.restaurant, when=start)
        self.pay(self.restaurant, when=start + timedelta(days=11 * 30))
        self.pay(self.restaurant, when=start + timedelta(days=12 * 30 + 5))

        self.assertEqual(self.amounts(), [("first", 49_500), ("percent", 19_800)])

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
        self.agent.first_percent, self.agent.percent = 40, 30
        self.agent.save()

        self.pay(self.restaurant)
        self.pay(self.restaurant)

        self.assertEqual(self.amounts(), [("first", 39_600), ("percent", 29_700)])


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
        self.fund()  # 49 500 + 2 × 19 800 = 89 100 — hali kam
        with self.assertRaises(services.WithdrawalError):
            self.request()
        self.pay(self.restaurant)  # + 19 800 = 108 900

        withdrawal = self.request()

        self.assertEqual(withdrawal.amount, 108_900)
        self.assertEqual(services.balance(self.agent).available, 0)
        self.assertEqual(services.balance(self.agent).pending, 108_900)
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

    def test_strangers_see_the_offer_not_anyones_data(self):
        for text in ("/start", agent_bot.BTN_BALANCE, "/start boshqa-token"):
            reply = self.say(text, chat_id="1")
            self.assertIn("Agent bo'lish", reply["text"])
            self.assertNotIn("Balans", reply["text"])
            self.assertEqual(reply["keyboard"], [[agent_bot.BTN_APPLY]])

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

        self.assertIn("49 500 so'm", self.say(agent_bot.BTN_BALANCE)["text"])
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


@override_settings(TELEGRAM_ADMIN_CHAT_ID=ADMIN_CHAT_ID)
class ApplicationTests(AgentTestCase):
    """Agent o'zi ariza to'ldiradi, platforma egasi Telegram'da tasdiqlaydi."""

    CHAT = "555"

    def setUp(self):
        super().setUp()
        self.bot = FakeChatBot()

    def say(self, text: str = "", contact=None, user_id=555):
        message = {"chat": {"id": self.CHAT, "type": "private"}, "from": {"id": user_id, "username": "vali"}}
        if text:
            message["text"] = text
        if contact:
            message["contact"] = contact
        with self.captureOnCommitCallbacks(execute=True):
            agent_bot.handle_message(self.bot, message)
        return self.bot.sent[-1]

    def apply(self, name="Vali Karimov", note="3 ta tanish restoran bor"):
        self.say("/start")
        self.say(agent_bot.BTN_APPLY)
        self.say(name)
        self.say(contact={"phone_number": "998901234567", "user_id": 555})
        self.say("Toshkent")
        return self.say(note)

    def agent(self) -> Agent:
        return Agent.objects.get(telegram_chat_id=self.CHAT)

    def test_full_application_reaches_the_owner(self):
        reply = self.apply()

        self.assertIn("Arizangiz yuborildi", reply["text"])
        agent = self.agent()
        self.assertTrue(agent.is_pending)
        self.assertFalse(agent.is_active)
        self.assertIsNone(agent.code)
        self.assertEqual((agent.name, agent.phone, agent.city), ("Vali Karimov", "+998901234567", "Toshkent"))
        owner = self.owner_bot.sent[-1]
        self.assertIn("Vali Karimov", owner["text"])
        self.assertIn("+998901234567", owner["text"])
        self.assertEqual(owner["buttons"], [("✅ Qabul qilish", f"ag_ok:{agent.pk}"), ("❌ Rad etish", f"ag_no:{agent.pk}")])

    def test_phone_button_is_offered_and_other_peoples_contacts_refused(self):
        self.say("/start")
        self.say(agent_bot.BTN_APPLY)
        ask = self.say("Vali Karimov")
        self.assertEqual(ask["keyboard"], [[agent_bot.BTN_CONTACT]])

        refused = self.say(contact={"phone_number": "998907777777", "user_id": 999})
        self.assertIn("o'zingizning raqamingizni", refused["text"])
        self.assertEqual(self.agent().phone, "")

    def test_bad_inputs_are_asked_again(self):
        self.say("/start")
        self.say(agent_bot.BTN_APPLY)
        self.assertIn("to'liq yozing", self.say("A")["text"])
        self.say("Vali Karimov")
        self.assertIn("tugmasini bosing", self.say("salom")["text"])
        self.say("+998 90 123 45 67")  # qo'lda yozilgan to'g'ri raqam ham qabul
        self.assertEqual(self.agent().phone, "+998901234567")

    def test_note_can_be_skipped(self):
        self.apply(note=agent_bot.BTN_SKIP)

        self.assertEqual(self.agent().note, "")

    def test_pending_applicant_cannot_use_agent_features(self):
        self.apply()

        self.assertIn("ko'rib chiqilmoqda", self.say(agent_bot.BTN_BALANCE)["text"])
        self.assertIn("ko'rib chiqilmoqda", self.say("/start")["text"])

    def press(self, action: str, agent: Agent, chat_id=ADMIN_CHAT_ID) -> FakeBot:
        bot = FakeBot()
        with self.captureOnCommitCallbacks(execute=True):
            OwnerBotCommand()._handle_callback(
                bot,
                {
                    "id": "cb",
                    "data": f"{action}:{agent.pk}",
                    "message": {"message_id": "5", "chat": {"id": chat_id}, "text": "ariza"},
                },
            )
        return bot

    def test_owner_approves_and_agent_gets_code_and_buttons(self):
        self.apply()

        bot = self.press("ag_ok", self.agent())

        agent = self.agent()
        self.assertEqual(agent.code, "VALI")
        self.assertTrue(agent.is_active)
        self.assertIn("kod VALI", bot.edited[0]["text"])
        welcome = self.agent_bot.sent[-1]
        self.assertIn("Tabriklaymiz", welcome["text"])
        self.assertEqual(welcome["keyboard"], agent_bot.KEYBOARD)
        # Endi agent — balansni ko'radi.
        self.assertIn("Balans", self.say(agent_bot.BTN_BALANCE)["text"])
        # Qayta bosish — hech narsa o'zgarmaydi.
        self.assertIn("allaqachon", self.press("ag_ok", agent).answered[0]["text"])

    def test_owner_rejects(self):
        self.apply()

        self.press("ag_no", self.agent())

        agent = self.agent()
        self.assertIsNotNone(agent.rejected_at)
        self.assertIsNone(agent.code)
        self.assertIn("qabul qila olmaymiz", self.agent_bot.sent[-1]["text"])
        self.assertIn("qabul qila olmaymiz", self.say(agent_bot.BTN_APPLY)["text"])

    def test_stranger_cannot_approve(self):
        self.apply()

        self.press("ag_ok", self.agent(), chat_id="1")

        self.assertTrue(self.agent().is_pending)

    def test_codes_are_unique_and_readable(self):
        make_agent(name="Vali Boshqa", code="VALI")
        self.assertEqual(services.generate_code("Vali Karimov"), "VALI2")
        self.assertEqual(services.generate_code("Алишер Усмонов"), "ALISHER")
        self.assertEqual(services.generate_code("Bo"), "BOAGE")
        self.assertEqual(services.generate_code("Oʻktam"), "OKTAM")

