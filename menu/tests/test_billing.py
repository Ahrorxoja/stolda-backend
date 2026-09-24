"""Qo'lda to'lov: chek yuklash, Telegram orqali tasdiqlash/rad etish."""

import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework_simplejwt.tokens import RefreshToken

from menu.billing_ledger import approve_receipt, reject_receipt
from menu.management.commands.telegram_bot import Command as TelegramBotCommand
from menu.models import Invoice, PaymentReceipt, Restaurant, Subscription
from menu.tasks import (
    RECEIPT_SEND_RETRIES,
    process_subscriptions,
    send_receipt_to_telegram,
)
from telegrambot import FakeBot, TelegramBot, TelegramError

from .factories import make_restaurant

ADMIN_CHAT_ID = "4242"


def auth_header(user) -> dict:
    token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def png_bytes(name="chek.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 90), (230, 230, 230)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ReceiptUploadTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.PAST_DUE)
        self.subscription = self.restaurant.subscription
        self.subscription.grace_ends_at = timezone.now() + timedelta(days=5)
        self.subscription.save(update_fields=["grace_ends_at"])
        self.owner = self.restaurant.owner

    def upload(self, period="month"):
        return self.client.post(
            reverse("billing-receipt"),
            {"period": period, "image": png_bytes()},
            **auth_header(self.owner),
        )

    def test_upload_creates_a_pending_receipt_with_the_server_side_amount(self):
        response = self.upload()

        self.assertEqual(response.status_code, 201, response.content)
        receipt = PaymentReceipt.objects.get(pk=response.json()["id"])
        self.assertEqual(receipt.status, PaymentReceipt.Status.PENDING)
        # Narx tarifdan olinadi, mijozdan emas.
        self.assertEqual(receipt.amount, self.subscription.plan.price_month)
        self.assertTrue(receipt.image.name.endswith(".webp"))

    def test_yearly_period_uses_the_yearly_price(self):
        self.upload(period="year")
        self.assertEqual(
            PaymentReceipt.objects.get().amount, self.subscription.plan.price_year
        )

    def test_second_upload_is_blocked_while_one_is_pending(self):
        self.upload()
        response = self.upload()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(PaymentReceipt.objects.count(), 1)

    def test_upload_does_not_change_the_subscription(self):
        self.upload()
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PAST_DUE)
        self.assertEqual(Invoice.objects.count(), 0)


class ReceiptReviewTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.SUSPENDED)
        self.subscription = self.restaurant.subscription
        self.receipt = PaymentReceipt.objects.create(
            subscription=self.subscription,
            image=png_bytes(),
            amount=99_000,
            period="month",
        )

    def test_approve_extends_the_subscription_and_restores_the_menu(self):
        invoice = approve_receipt(self.receipt)

        self.assertIsNotNone(invoice)
        self.receipt.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.APPROVED)
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(self.subscription.current_period_end)
        self.assertEqual(invoice.receipt, self.receipt)

        response = self.client.get(reverse("public-menu", args=[self.restaurant.slug]))
        self.assertNotIn("active", response.json())

    def test_approving_twice_creates_only_one_invoice(self):
        approve_receipt(self.receipt)
        second = approve_receipt(self.receipt)

        self.assertIsNone(second)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_reject_leaves_the_subscription_untouched(self):
        self.assertTrue(reject_receipt(self.receipt, note="Chek o'qilmadi."))

        self.receipt.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.REJECTED)
        self.assertEqual(self.receipt.note, "Chek o'qilmadi.")
        self.assertEqual(self.subscription.status, Subscription.Status.SUSPENDED)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_rejecting_an_approved_receipt_does_nothing(self):
        approve_receipt(self.receipt)
        self.assertFalse(reject_receipt(self.receipt))

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.APPROVED)

    def test_paying_early_keeps_the_remaining_days(self):
        """Muddat tugamasdan to'lansa, qolgan kunlar kuyib ketmasligi kerak."""
        old_end = timezone.now() + timedelta(days=8)
        self.subscription.status = Subscription.Status.ACTIVE
        self.subscription.current_period_end = old_end
        self.subscription.save(update_fields=["status", "current_period_end"])

        invoice = approve_receipt(self.receipt)

        self.subscription.refresh_from_db()
        # Yangi davr eskisining oxiridan boshlanadi: 8 + 30 kun.
        self.assertAlmostEqual(
            (self.subscription.current_period_end - old_end).days, 30, delta=1
        )
        self.assertAlmostEqual((invoice.period_start - old_end).total_seconds(), 0, delta=5)

    def test_paying_after_the_period_expired_starts_from_today(self):
        """O'tib ketgan muddatdan hisoblansa, mijoz to'liq davrni olmasdi."""
        self.subscription.status = Subscription.Status.SUSPENDED
        self.subscription.current_period_end = timezone.now() - timedelta(days=40)
        self.subscription.save(update_fields=["status", "current_period_end"])

        approve_receipt(self.receipt)

        self.subscription.refresh_from_db()
        self.assertAlmostEqual(
            (self.subscription.current_period_end - timezone.now()).days, 29, delta=1
        )

    def test_approve_follows_the_period_on_the_receipt(self):
        self.receipt.period = "year"
        self.receipt.save(update_fields=["period"])

        approve_receipt(self.receipt)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.period, "year")
        self.assertGreater(
            self.subscription.current_period_end, timezone.now() + timedelta(days=300)
        )


def _failing_bot() -> FakeBot:
    """Tarmoq uzilgan bot — `send_receipt` har doim xato beradi."""
    bot = FakeBot()
    bot.send_receipt = lambda **kwargs: (_ for _ in ()).throw(
        TelegramError("Telegram'ga ulanib bo'lmadi (sendPhoto).")
    )
    return bot


class TelegramSendTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.receipt = PaymentReceipt.objects.create(
            subscription=self.restaurant.subscription,
            image=png_bytes(),
            amount=99_000,
            period="month",
        )

    def test_send_records_the_message_id(self):
        bot = FakeBot()
        with patch("menu.tasks.get_bot", return_value=bot):
            send_receipt_to_telegram(self.receipt.pk)

        self.receipt.refresh_from_db()
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(self.receipt.telegram_message_id, bot.sent[0]["message_id"])
        self.assertEqual(bot.sent[0]["approve"], f"approve:{self.receipt.pk}")
        self.assertIn(self.restaurant.name, bot.sent[0]["caption"])

    def test_send_failure_asks_for_a_retry(self):
        """Telegram qisqa uzilsa chek yo'qolmasligi kerak — qayta urinadi."""
        with patch("menu.tasks.get_bot", return_value=_failing_bot()):
            with self.assertRaises(TelegramError):
                send_receipt_to_telegram(self.receipt.pk)

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.PENDING)

    def test_receipt_is_rejected_once_the_retries_run_out(self):
        """Aks holda chek abadiy `pending` bo'lib, yangisini yuklashni to'sib qo'yardi."""
        with patch("menu.tasks.get_bot", return_value=_failing_bot()):
            result = send_receipt_to_telegram.apply(
                args=[self.receipt.pk], retries=RECEIPT_SEND_RETRIES
            ).get()

        self.receipt.refresh_from_db()
        self.assertEqual(result["rejected"], self.receipt.pk)
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.REJECTED)
        self.assertIn("qaytadan yuklang", self.receipt.note)

    def test_owner_can_upload_again_after_a_failed_send(self):
        with patch("menu.tasks.get_bot", return_value=_failing_bot()):
            send_receipt_to_telegram.apply(
                args=[self.receipt.pk], retries=RECEIPT_SEND_RETRIES
            ).get()

        response = self.client.post(
            reverse("billing-receipt"),
            {"period": "month", "image": png_bytes()},
            **auth_header(self.restaurant.owner),
        )

        self.assertEqual(response.status_code, 201)


class TelegramTokenSafetyTests(TestCase):
    """Token API manzilining ichida — u hech qachon xato matniga tushmasligi kerak."""

    token = "123456:SUPER-SECRET-TOKEN"

    def test_connection_error_message_hides_the_token(self):
        import requests

        session = requests.Session()
        session.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.ConnectionError(f"failed to reach https://api.telegram.org/bot{self.token}/sendPhoto")
        )
        bot = TelegramBot(self.token, "42", session=session)

        with self.assertRaises(TelegramError) as caught:
            bot.answer_callback("1")

        self.assertNotIn(self.token, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_unauthorized_message_hides_the_token(self):
        import requests

        class Response:
            status_code = 401

            def json(self):
                return {"ok": False, "description": f"bot{TelegramTokenSafetyTests.token} is wrong"}

        session = requests.Session()
        session.post = lambda *args, **kwargs: Response()
        bot = TelegramBot(self.token, "42", session=session)

        with self.assertRaises(TelegramError) as caught:
            bot.answer_callback("1")

        self.assertNotIn(self.token, str(caught.exception))


class ProcessSubscriptionsTests(TestCase):
    """To'lov qo'lda — kunlik vazifa endi hech narsa yechmaydi."""

    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.ACTIVE)
        self.subscription = self.restaurant.subscription

    def test_expired_period_becomes_past_due(self):
        self.subscription.current_period_end = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["current_period_end"])

        process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PAST_DUE)
        self.assertIsNotNone(self.subscription.grace_ends_at)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_past_due_still_serves_the_full_menu(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.grace_ends_at = timezone.now() + timedelta(days=3)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        response = self.client.get(reverse("public-menu", args=[self.restaurant.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("active", response.json())

    def test_grace_expiry_suspends(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.grace_ends_at = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.SUSPENDED)


class DailyReminderTests(TestCase):
    """Muddat tugashidan oldin platforma egasiga Telegram'da xabar boradi."""

    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.TRIALING)
        self.subscription = self.restaurant.subscription

    def run_task(self) -> FakeBot:
        bot = FakeBot()
        with patch("menu.tasks.get_bot", return_value=bot):
            process_subscriptions()
        return bot

    def test_trial_ending_in_three_days_is_reported(self):
        self.subscription.trial_ends_at = timezone.now() + timedelta(days=3)
        self.subscription.save(update_fields=["trial_ends_at"])

        bot = self.run_task()

        self.assertEqual(len(bot.sent), 1)
        text = bot.sent[0]["text"]
        self.assertIn(self.restaurant.name, text)
        self.assertIn("3 kundan keyin tugaydi", text)

    def test_suspension_is_reported(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.grace_ends_at = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        bot = self.run_task()

        self.assertIn("menyu to'xtatildi", bot.sent[0]["text"])

    def test_nothing_is_sent_when_there_is_nothing_to_report(self):
        self.subscription.trial_ends_at = timezone.now() + timedelta(days=30)
        self.subscription.save(update_fields=["trial_ends_at"])

        self.assertEqual(self.run_task().sent, [])

    def test_a_broken_bot_does_not_break_the_subscription_run(self):
        """Eslatma yetib bormasa ham obuna holati to'g'ri hisoblanishi kerak."""
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.grace_ends_at = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        broken = FakeBot()
        broken.send_message = lambda text: (_ for _ in ()).throw(
            TelegramError("Telegram'ga ulanib bo'lmadi (sendMessage).")
        )
        with patch("menu.tasks.get_bot", return_value=broken):
            stats = process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.SUSPENDED)
        self.assertEqual(stats["suspended"], 1)


@override_settings(TELEGRAM_ADMIN_CHAT_ID=ADMIN_CHAT_ID)
class CallbackRoutingTests(TestCase):
    """`telegram_bot` buyrug'i tugmalarni to'g'ri yo'naltiradimi.

    Jonli sinovda ✅ va ❌ ni adashtirib bosish oson — marshrut shu yerda
    qotiriladi, toki "rad etdim" degan bosish obunani uzaytirib yubormasin.
    """

    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.SUSPENDED)
        self.subscription = self.restaurant.subscription
        self.receipt = PaymentReceipt.objects.create(
            subscription=self.subscription,
            image=png_bytes(),
            amount=99_000,
            period="month",
        )
        self.command = TelegramBotCommand()

    def press(self, action: str, chat_id=ADMIN_CHAT_ID) -> FakeBot:
        bot = FakeBot()
        self.command._handle_callback(
            bot,
            {
                "id": "cb-1",
                "data": f"{action}:{self.receipt.pk}",
                "message": {"message_id": "77", "chat": {"id": chat_id}, "caption": "chek"},
            },
        )
        return bot

    def test_reject_button_does_not_extend_the_subscription(self):
        bot = self.press("reject")

        self.receipt.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.REJECTED)
        self.assertEqual(self.subscription.status, Subscription.Status.SUSPENDED)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertIn("Rad etildi", bot.answered[0]["text"])

    def test_approve_button_activates_the_subscription(self):
        bot = self.press("approve")

        self.receipt.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.APPROVED)
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertIn("Tasdiqlandi", bot.answered[0]["text"])

    def test_a_stranger_chat_cannot_decide(self):
        bot = self.press("approve", chat_id="999999")

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.PENDING)
        self.assertEqual(bot.answered, [])
        self.assertEqual(Invoice.objects.count(), 0)

    def test_second_press_changes_nothing(self):
        self.press("reject")
        bot = self.press("approve")

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, PaymentReceipt.Status.REJECTED)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertIn("allaqachon", bot.answered[0]["text"])


class BillingApiTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.owner = self.restaurant.owner

    @override_settings(PAYMENT_CARD_NUMBER="8600 1234", PAYMENT_CARD_HOLDER="ISM")
    def test_overview_includes_the_card_and_plans(self):
        response = self.client.get(reverse("billing"), **auth_header(self.owner))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["payment"], {"card_number": "8600 1234", "card_holder": "ISM"})
        self.assertIsNone(data["pending_receipt"])
        self.assertTrue(any(plan["code"] == "standard" for plan in data["plans"]))

    def test_overview_shows_the_pending_receipt(self):
        PaymentReceipt.objects.create(
            subscription=self.restaurant.subscription,
            image=png_bytes(),
            amount=99_000,
            period="month",
        )

        data = self.client.get(reverse("billing"), **auth_header(self.owner)).json()

        self.assertEqual(data["pending_receipt"]["status"], "pending")
        self.assertTrue(data["pending_receipt"]["image_url"])

    def test_invoice_links_back_to_its_receipt_image(self):
        receipt = PaymentReceipt.objects.create(
            subscription=self.restaurant.subscription,
            image=png_bytes(),
            amount=99_000,
            period="month",
        )
        approve_receipt(receipt)

        data = self.client.get(reverse("billing"), **auth_header(self.owner)).json()

        self.assertEqual(len(data["invoices"]), 1)
        self.assertTrue(data["invoices"][0]["receipt_url"])

    def test_other_users_see_their_own_billing_only(self):
        stranger = get_user_model().objects.create_user(username="begona@example.com")

        response = self.client.get(reverse("billing"), **auth_header(stranger))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Restaurant.objects.filter(owner=stranger).count(), 0)
