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
from agents.rules import RULES_VERSION
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
    defaults = {
        "name": "Ali Valiyev",
        "code": "ALI25",
        "phone": "+998901112233",
        # Qoidalarga rozi — alohida testlardan tashqari hammasi shu holatda.
        "rules_version": RULES_VERSION,
        "rules_accepted_at": timezone.now(),
    }
    return Agent.objects.create(**{**defaults, **kwargs})


class AgentTestCase(TestCase):
    """Soxta botlar: agentga va platforma egasiga ketgan xabarlarni ushlaydi."""

    def setUp(self):
        self.agent_bot = FakeChatBot()
        self.owner_bot = FakeBot()
        patches = [
            patch("agents.services.get_agent_bot", return_value=self.agent_bot),
            patch("agents.services.get_bot", return_value=self.owner_bot),
            patch("agents.journal.get_bot", return_value=self.owner_bot),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def pay(self, restaurant: Restaurant, period="month", when=None) -> Invoice:
        """Chek yuklanib, tasdiqlangandek — `on_commit` xabarlari bilan."""
        subscription = restaurant.subscription
        amount = subscription.plan.price_for(period)
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

    def test_next_payments_continue_while_agent_is_active(self):
        start = timezone.now()
        self.pay(self.restaurant, when=start)
        self.pay(self.restaurant, when=start + timedelta(days=13 * 30))
        self.pay(self.restaurant, when=start + timedelta(days=3 * 365))

        self.assertEqual(self.amounts(), [("first", 49_500), ("percent", 19_800), ("percent", 19_800)])

    def test_deactivated_agent_stops_earning_but_keeps_balance(self):
        self.pay(self.restaurant)
        self.agent.is_active = False
        self.agent.save()

        self.pay(self.restaurant)

        self.assertEqual(self.amounts(), [("first", 49_500)])
        self.assertEqual(services.balance(self.agent).available, 49_500)

    def test_optional_months_limit_per_agent(self):
        self.agent.months = 12
        self.agent.save()
        start = timezone.now()
        self.pay(self.restaurant, when=start)
        self.pay(self.restaurant, when=start + timedelta(days=11 * 30))
        self.pay(self.restaurant, when=start + timedelta(days=12 * 30 + 5))

        self.assertEqual(self.amounts(), [("first", 49_500), ("percent", 19_800)])

    def test_restaurant_moved_to_another_agent(self):
        self.pay(self.restaurant)
        other = make_agent(name="Jasur", code="JASUR")
        Restaurant.objects.filter(pk=self.restaurant.pk).update(agent=other)
        self.restaurant.refresh_from_db()

        self.pay(self.restaurant)

        self.assertEqual(AgentEarning.objects.filter(agent=other).count(), 1)
        self.assertEqual(AgentEarning.objects.filter(agent=self.agent).count(), 1)

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
        self.say(agent_bot.BTN_ACCEPT)
        self.say(name)
        self.say(contact={"phone_number": "998901234567", "user_id": 555})
        self.say("Toshkent shahri")
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
        self.assertEqual((agent.name, agent.phone, agent.city), ("Vali Karimov", "+998901234567", "Toshkent shahri"))
        owner = self.owner_bot.sent[-1]
        self.assertIn("Vali Karimov", owner["text"])
        self.assertIn("+998901234567", owner["text"])
        self.assertEqual(owner["buttons"], [("✅ Qabul qilish", f"ag_ok:{agent.pk}"), ("❌ Rad etish", f"ag_no:{agent.pk}")])

    def test_phone_button_is_offered_and_other_peoples_contacts_refused(self):
        self.say("/start")
        self.say(agent_bot.BTN_APPLY)
        self.say(agent_bot.BTN_ACCEPT)
        ask = self.say("Vali Karimov")
        self.assertEqual(ask["keyboard"], [[agent_bot.BTN_CONTACT]])

        refused = self.say(contact={"phone_number": "998907777777", "user_id": 999})
        self.assertIn("o'zingizning raqamingizni", refused["text"])
        self.assertEqual(self.agent().phone, "")

    def test_bad_inputs_are_asked_again(self):
        self.say("/start")
        self.say(agent_bot.BTN_APPLY)
        self.say(agent_bot.BTN_ACCEPT)
        self.assertIn("to'liq yozing", self.say("A")["text"])
        self.say("Vali Karimov")
        self.assertIn("tugmasini bosing", self.say("salom")["text"])
        self.say("+998 90 123 45 67")  # qo'lda yozilgan to'g'ri raqam ham qabul
        self.assertEqual(self.agent().phone, "+998901234567")

    def to_phone_step(self):
        self.say("/start")
        self.say(agent_bot.BTN_APPLY)
        self.say(agent_bot.BTN_ACCEPT)
        return self.say("Vali Karimov")

    def test_phone_can_be_typed_and_is_validated(self):
        ask = self.to_phone_step()
        self.assertIn("yozing", ask["text"])  # tugmadan tashqari yozish ham mumkin

        for wrong in ("12345", "+7 916 123 45 67", "998 00 123 45 67", "+998 90 123 45 6", "salom"):
            with self.subTest(wrong=wrong):
                self.assertIn("Raqam noto'g'ri", self.say(wrong)["text"])
        self.assertEqual(self.agent().phone, "")

        self.say("90 123-45-67")
        self.assertEqual(self.agent().phone, "+998901234567")

    def test_foreign_contact_is_refused(self):
        self.to_phone_step()

        reply = self.say(contact={"phone_number": "79161234567", "user_id": 555})

        self.assertIn("O'zbekiston raqami emas", reply["text"])
        self.assertEqual(self.agent().phone, "")

    def test_all_fourteen_regions_are_offered(self):
        self.to_phone_step()
        ask = self.say("+998 90 123 45 67")

        regions = [name for row in ask["keyboard"] for name in row]
        self.assertEqual(len(regions), 14)
        self.assertIn("Qoraqalpog'iston", regions)
        self.assertIn("Toshkent viloyati", regions)

    def test_region_must_come_from_the_list(self):
        self.to_phone_step()
        self.say("+998 90 123 45 67")

        self.assertIn("tugmalardan tanlang", self.say("Moskva")["text"])
        self.say("farg‘ona")  # boshqa apostrof, kichik harf
        self.assertEqual(self.agent().city, "Farg'ona")

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
        welcome, guide = self.agent_bot.sent[-2:]
        self.assertIn("Tabriklaymiz", welcome["text"])
        self.assertEqual(welcome["keyboard"], agent_bot.KEYBOARD)
        self.assertEqual(guide["document"], "stolda-agent-qollanma.pdf")  # qo'llanma darhol
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


class ReminderTests(AgentTestCase):
    """Kunlik tekshiruv agentga: to'lov yaqin, muddat o'tdi, menyu to'xtadi."""

    def setUp(self):
        super().setUp()
        self.agent = make_agent(telegram_chat_id=AGENT_CHAT_ID)
        self.restaurant = make_restaurant(phone="+998901234567")
        services.attach(self.restaurant, self.agent)
        self.agent_bot.sent.clear()
        self.subscription = self.restaurant.subscription

    def run_daily(self):
        from menu.tasks import process_subscriptions

        with patch("menu.tasks.get_bot", return_value=FakeBot()), self.captureOnCommitCallbacks(execute=True):
            process_subscriptions()
        return self.agent_bot.texts(AGENT_CHAT_ID)

    def set_end(self, days: float, status="active"):
        end = timezone.now() + timedelta(days=days)
        Subscription.objects.filter(pk=self.subscription.pk).update(
            status=status,
            current_period_end=end,
            trial_ends_at=end if status == "trialing" else None,
        )

    def test_payment_due_in_three_days(self):
        self.set_end(3.2)

        texts = self.run_daily()

        self.assertEqual(len(texts), 1)
        self.assertIn("to'lov muddati", texts[0])
        self.assertIn("Zamin", texts[0])
        self.assertIn("+998 90 123 45 67", texts[0])

    def test_trial_ending_asks_for_first_payment(self):
        self.set_end(3.2, status="trialing")

        self.assertIn("Birinchi to'lovni eslatib", self.run_daily()[0])

    def test_overdue_and_suspended(self):
        self.set_end(-0.1)
        self.assertIn("muddati o'tdi", self.run_daily()[-1])

        Subscription.objects.filter(pk=self.subscription.pk).update(
            grace_ends_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertIn("menyu to'xtatildi", self.run_daily()[-1])

    def test_one_message_for_several_restaurants(self):
        second = make_restaurant(slug="ikkinchi", name="Ikkinchi")
        services.attach(second, self.agent)
        self.agent_bot.sent.clear()
        end = timezone.now() + timedelta(days=3.2)
        Subscription.objects.update(status="active", current_period_end=end)

        texts = self.run_daily()

        self.assertEqual(len(texts), 1)
        self.assertIn("Zamin", texts[0])
        self.assertIn("Ikkinchi", texts[0])

    def test_inactive_agent_gets_no_reminders(self):
        Agent.objects.filter(pk=self.agent.pk).update(is_active=False)
        self.set_end(3.2)

        self.assertEqual(self.run_daily(), [])

    def test_quiet_day_sends_nothing(self):
        self.set_end(20)

        self.assertEqual(self.run_daily(), [])


class RulesTests(AgentTestCase):
    """Ariza oldidan to'liq qoidalar; rozilik versiya va sana bilan yoziladi."""

    CHAT = "888"

    def setUp(self):
        super().setUp()
        self.bot = FakeChatBot()

    def say(self, text: str, chat_id=None):
        agent_bot.handle_message(
            self.bot,
            {"chat": {"id": chat_id or self.CHAT, "type": "private"}, "from": {"id": 888}, "text": text},
        )
        return self.bot.sent[-1]

    def test_apply_shows_full_rules_before_the_form(self):
        self.say("/start")
        before = len(self.bot.sent)

        last = self.say(agent_bot.BTN_APPLY)

        parts = [item["text"] for item in self.bot.sent[before:]]
        self.assertEqual(len(parts), 2)
        self.assertIn("agent qoidalari", parts[0])
        self.assertIn("birinchi to'lovidan 50%", parts[0])
        self.assertIn("Taqiqlanadi", parts[1])
        self.assertTrue(all(len(part) < 4096 for part in parts))
        self.assertEqual(last["keyboard"], agent_bot.RULES_KEYBOARD)
        self.assertFalse(Agent.objects.exists())  # rozi bo'lmaguncha ariza yo'q

    def test_cancel_goes_back_without_applying(self):
        self.say(agent_bot.BTN_APPLY)

        reply = self.say(agent_bot.BTN_CANCEL)

        self.assertEqual(reply["keyboard"], [[agent_bot.BTN_APPLY]])
        self.assertFalse(Agent.objects.exists())

    def test_accepting_records_version_and_time_and_starts_form(self):
        self.say(agent_bot.BTN_APPLY)

        reply = self.say(agent_bot.BTN_ACCEPT)

        agent = Agent.objects.get(telegram_chat_id=self.CHAT)
        self.assertEqual(agent.rules_version, RULES_VERSION)
        self.assertIsNotNone(agent.rules_accepted_at)
        self.assertIn("1/4", reply["text"])

    def test_manually_added_agent_accepts_rules_first(self):
        agent = make_agent(rules_version="", rules_accepted_at=None)

        shown = self.say(f"/start {agent.invite_token}")
        self.assertEqual(shown["keyboard"], agent_bot.RULES_KEYBOARD)
        self.assertEqual(self.say(agent_bot.BTN_BALANCE)["keyboard"], agent_bot.RULES_KEYBOARD)

        welcome = self.say(agent_bot.BTN_ACCEPT)

        agent.refresh_from_db()
        self.assertEqual(agent.rules_version, RULES_VERSION)
        self.assertIn("ALI25", welcome["text"])
        self.assertEqual(welcome["keyboard"], agent_bot.KEYBOARD)

    def test_new_rules_version_asks_again(self):
        agent = make_agent(telegram_chat_id=self.CHAT)
        self.assertIn("Balans", self.say(agent_bot.BTN_BALANCE)["text"])

        newer = str(int(RULES_VERSION) + 1)
        with patch("agents.rules.RULES_VERSION", newer), patch("agents.bot.RULES_VERSION", newer):
            self.assertEqual(self.say(agent_bot.BTN_BALANCE)["keyboard"], agent_bot.RULES_KEYBOARD)
            self.say(agent_bot.BTN_ACCEPT)
            agent.refresh_from_db()
            self.assertEqual(agent.rules_version, newer)
            self.assertIn("Balans", self.say(agent_bot.BTN_BALANCE)["text"])

    def test_agent_can_reread_rules(self):
        make_agent(telegram_chat_id=self.CHAT)

        last = self.say("/qoidalar")

        self.assertIn("Aloqa", last["text"])
        self.assertEqual(last["keyboard"], agent_bot.KEYBOARD)



class MenuCommandsTests(TestCase):
    def test_every_menu_command_is_handled(self):
        from agents import bot as agent_bot

        names = [name for name, _ in agent_bot.MENU_COMMANDS]
        self.assertEqual(names[0], "start")
        for name in names[1:]:
            with self.subTest(name=name):
                self.assertIn(f"/{name}", agent_bot._COMMANDS)

    def test_rules_button_shows_rules_and_keeps_keyboard(self):
        from agents import bot as agent_bot
        from agents.rules import RULES_VERSION
        from django.utils import timezone
        from telegrambot import FakeChatBot

        agent = Agent.objects.create(
            name="Ali", code="ALI", telegram_chat_id="77", rules_version=RULES_VERSION, rules_accepted_at=timezone.now()
        )
        bot = FakeChatBot()
        agent_bot.handle_message(bot, {"chat": {"id": agent.telegram_chat_id, "type": "private"}, "text": agent_bot.BTN_RULES})

        self.assertIn("agent qoidalari", bot.sent[0]["text"])
        self.assertEqual(bot.sent[-1]["keyboard"], agent_bot.KEYBOARD)

    def test_help_sends_pdf_guide(self):
        from agents import bot as agent_bot
        from agents.rules import RULES_VERSION
        from agents.services import GUIDE_PATH
        from django.utils import timezone
        from telegrambot import FakeChatBot

        self.assertTrue(GUIDE_PATH.exists())
        agent = Agent.objects.create(
            name="Ali", code="ALI", telegram_chat_id="78", rules_version=RULES_VERSION, rules_accepted_at=timezone.now()
        )
        bot = FakeChatBot()
        agent_bot.handle_message(bot, {"chat": {"id": agent.telegram_chat_id, "type": "private"}, "text": agent_bot.BTN_HELP})

        self.assertIn("Qanday ishlaydi", bot.sent[0]["text"])
        self.assertEqual(bot.sent[1]["document"], "stolda-agent-qollanma.pdf")
