"""Click SHOP API: havola yaratish, Prepare, Complete, soliq cheki."""

import hashlib
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from menu import click
from menu.models import ClickPayment, Invoice, Subscription
from menu.tasks import notify_click_payment, submit_click_fiscal
from telegrambot import FakeBot

from .factories import make_restaurant

CLICK = {
    "CLICK_SERVICE_ID": "111",
    "CLICK_MERCHANT_ID": "222",
    "CLICK_SECRET_KEY": "maxfiy",
    "CLICK_MERCHANT_USER_ID": "333",
}


def auth_header(user) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


def signed(params: dict, secret: str = "maxfiy") -> dict:
    """Click qanday imzolasa, shunday: md5 matnlar ketma-ketligidan."""
    parts = [params["click_trans_id"], params["service_id"], secret, params["merchant_trans_id"]]
    if "merchant_prepare_id" in params:
        parts.append(params["merchant_prepare_id"])
    parts += [params["amount"], params["action"], params["sign_time"]]
    return {**params, "sign_string": hashlib.md5("".join(map(str, parts)).encode()).hexdigest()}


@override_settings(**CLICK)
class ClickTestCase(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.PAST_DUE)
        self.subscription = self.restaurant.subscription
        self.owner = self.restaurant.owner
        self.payment = ClickPayment.objects.create(
            subscription=self.subscription, amount=549_000, period="half"
        )

    def prepare(self, **overrides):
        params = {
            "click_trans_id": "9001",
            "service_id": "111",
            "click_paydoc_id": "7001",
            "merchant_trans_id": str(self.payment.pk),
            "amount": "549000.00",
            "action": "0",
            "error": "0",
            "error_note": "Success",
            "sign_time": "2026-10-05 10:00:00",
        }
        params.update(overrides.pop("params", {}))
        data = signed(params)
        data.update(overrides)
        # Click `application/x-www-form-urlencoded` yuboradi.
        return self.client.post(reverse("click-prepare"), data).json()

    def complete(self, **overrides):
        params = {
            "click_trans_id": "9001",
            "service_id": "111",
            "click_paydoc_id": "7001",
            "merchant_trans_id": str(self.payment.pk),
            "merchant_prepare_id": str(self.payment.pk),
            "amount": "549000.00",
            "action": "1",
            "error": "0",
            "error_note": "Success",
            "sign_time": "2026-10-05 10:00:05",
        }
        params.update(overrides.pop("params", {}))
        data = signed(params)
        data.update(overrides)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("click-complete"), data).json()


class CreateTests(ClickTestCase):
    def test_link_carries_the_server_side_amount(self):
        response = self.client.post(
            reverse("billing-click"), {"period": "year"}, **auth_header(self.owner)
        )

        self.assertEqual(response.status_code, 201, response.content)
        payment = ClickPayment.objects.get(pk=response.json()["id"])
        self.assertEqual(payment.amount, 990_000)
        self.assertEqual(payment.status, ClickPayment.Status.CREATED)

        url = urlparse(response.json()["url"])
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", click.PAY_URL)
        self.assertEqual(query["service_id"], "111")
        self.assertEqual(query["merchant_id"], "222")
        self.assertEqual(query["amount"], "990000")
        self.assertEqual(query["transaction_param"], str(payment.pk))
        self.assertTrue(query["return_url"].endswith("/admin/billing?click=done"))

    def test_telegram_app_returns_to_the_app(self):
        response = self.client.post(
            reverse("billing-click"),
            {"period": "month", "return_to": "app"},
            **auth_header(self.owner),
        )
        self.assertIn("%2Fapp", response.json()["url"])

    def test_unknown_period_is_refused(self):
        response = self.client.post(
            reverse("billing-click"), {"period": "week"}, **auth_header(self.owner)
        )
        self.assertEqual(response.status_code, 400)

    def test_period_without_a_price_is_refused(self):
        plan = self.subscription.plan
        plan.price_half_year = 0
        plan.save(update_fields=["price_half_year"])

        response = self.client.post(
            reverse("billing-click"), {"period": "half"}, **auth_header(self.owner)
        )
        self.assertEqual(response.status_code, 400)

    def test_needs_login(self):
        self.assertEqual(self.client.post(reverse("billing-click"), {"period": "month"}).status_code, 401)

    def test_overview_says_click_is_on(self):
        data = self.client.get(reverse("billing"), **auth_header(self.owner)).json()
        self.assertTrue(data["click_enabled"])


@override_settings(CLICK_SERVICE_ID="", CLICK_MERCHANT_ID="", CLICK_SECRET_KEY="")
class DisabledTests(ClickTestCase):
    def test_no_keys_no_button(self):
        data = self.client.get(reverse("billing"), **auth_header(self.owner)).json()
        self.assertFalse(data["click_enabled"])

        response = self.client.post(
            reverse("billing-click"), {"period": "month"}, **auth_header(self.owner)
        )
        self.assertEqual(response.status_code, 400)

    def test_callbacks_are_refused(self):
        self.assertEqual(self.prepare()["error"], click.SIGN_FAILED)


class PrepareTests(ClickTestCase):
    def test_success(self):
        reply = self.prepare()

        self.assertEqual(reply["error"], click.OK)
        self.assertEqual(reply["merchant_prepare_id"], self.payment.pk)
        self.assertEqual(reply["click_trans_id"], 9001)
        self.assertEqual(reply["merchant_trans_id"], str(self.payment.pk))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, ClickPayment.Status.PREPARED)
        self.assertEqual(self.payment.click_trans_id, 9001)
        self.assertEqual(self.payment.click_paydoc_id, 7001)

    def test_amount_without_decimals_also_matches(self):
        self.assertEqual(self.prepare(params={"amount": "549000"})["error"], click.OK)

    def test_wrong_signature(self):
        self.assertEqual(self.prepare(sign_string="0" * 32)["error"], click.SIGN_FAILED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, ClickPayment.Status.CREATED)

    def test_signature_from_another_secret(self):
        params = signed(
            {
                "click_trans_id": "9001",
                "service_id": "111",
                "merchant_trans_id": str(self.payment.pk),
                "amount": "549000",
                "action": "0",
                "sign_time": "2026-10-05 10:00:00",
            },
            secret="boshqa",
        )
        reply = self.client.post(reverse("click-prepare"), params).json()
        self.assertEqual(reply["error"], click.SIGN_FAILED)

    def test_wrong_service(self):
        self.assertEqual(self.prepare(params={"service_id": "999"})["error"], click.SIGN_FAILED)

    def test_wrong_amount(self):
        self.assertEqual(self.prepare(params={"amount": "1000"})["error"], click.BAD_AMOUNT)

    def test_unknown_order(self):
        self.assertEqual(self.prepare(params={"merchant_trans_id": "999999"})["error"], click.ORDER_NOT_FOUND)

    def test_wrong_action(self):
        self.assertEqual(self.prepare(params={"action": "1"})["error"], click.ACTION_NOT_FOUND)

    def test_missing_field(self):
        reply = self.client.post(reverse("click-prepare"), {"click_trans_id": "1"}).json()
        self.assertEqual(reply["error"], click.BAD_REQUEST)

    def test_already_paid(self):
        self.prepare()
        self.complete()
        self.assertEqual(self.prepare(params={"click_trans_id": "9002"})["error"], click.ALREADY_PAID)


@patch("menu.tasks.get_bot")
class CompleteTests(ClickTestCase):
    def test_success_extends_the_subscription(self, get_bot):
        get_bot.return_value = bot = FakeBot()
        self.prepare()
        reply = self.complete()

        self.assertEqual(reply["error"], click.OK, reply)
        invoice = Invoice.objects.get()
        self.assertEqual(reply["merchant_confirm_id"], invoice.pk)
        self.assertEqual(invoice.provider_payment_id, "click_9001")
        self.assertEqual(invoice.amount, 549_000)
        self.assertEqual((invoice.period_end - invoice.period_start).days, 183)

        self.payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.payment.status, ClickPayment.Status.PAID)
        self.assertEqual(self.payment.invoice, invoice)
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(self.subscription.period, "half")
        self.assertGreater(self.subscription.current_period_end, timezone.now() + timedelta(days=180))
        # Platforma egasiga xabar.
        self.assertIn("Click", bot.sent[0]["text"])
        self.assertIn("549 000", bot.sent[0]["text"])

    def test_repeated_complete_pays_once(self, get_bot):
        get_bot.return_value = FakeBot()
        self.prepare()
        self.complete()
        reply = self.complete()

        self.assertEqual(reply["error"], click.ALREADY_PAID)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_click_error_cancels_the_order(self, get_bot):
        self.prepare()
        reply = self.complete(params={"error": "-5017", "error_note": "Insufficient funds"})

        self.assertEqual(reply["error"], click.CANCELED)
        self.payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.payment.status, ClickPayment.Status.CANCELED)
        self.assertEqual(self.payment.error_note, "Insufficient funds")
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(self.subscription.status, Subscription.Status.PAST_DUE)
        # Bekor qilingan buyurtma qayta tayyorlanmaydi.
        self.assertEqual(self.prepare(params={"click_trans_id": "9002"})["error"], click.CANCELED)

    def test_complete_without_prepare(self, get_bot):
        reply = self.complete()
        self.assertEqual(reply["error"], click.TRANSACTION_NOT_FOUND)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_wrong_prepare_id(self, get_bot):
        self.prepare()
        reply = self.complete(params={"merchant_prepare_id": "424242"})
        self.assertEqual(reply["error"], click.TRANSACTION_NOT_FOUND)

    def test_other_transaction(self, get_bot):
        self.prepare()
        reply = self.complete(params={"click_trans_id": "9555"})
        self.assertEqual(reply["error"], click.TRANSACTION_NOT_FOUND)

    def test_wrong_amount(self, get_bot):
        self.prepare()
        self.assertEqual(self.complete(params={"amount": "99000"})["error"], click.BAD_AMOUNT)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_signature_includes_the_prepare_id(self, get_bot):
        self.prepare()
        reply = self.complete(sign_string="f" * 32)
        self.assertEqual(reply["error"], click.SIGN_FAILED)

    def test_ledger_failure_is_reported_for_a_retry(self, get_bot):
        self.prepare()
        with patch("menu.click.record_payment", side_effect=RuntimeError("db")):
            reply = self.complete()

        self.assertEqual(reply["error"], click.UPDATE_FAILED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, ClickPayment.Status.PREPARED)

        # Click qayta urinadi — endi o'tadi.
        get_bot.return_value = FakeBot()
        self.assertEqual(self.complete()["error"], click.OK)

    def test_retry_after_failed_attempt_uses_new_transaction(self, get_bot):
        get_bot.return_value = FakeBot()
        self.prepare()
        self.prepare(params={"click_trans_id": "9002"})
        reply = self.complete(params={"click_trans_id": "9002"})

        self.assertEqual(reply["error"], click.OK)
        self.assertEqual(Invoice.objects.get().provider_payment_id, "click_9002")


@override_settings(
    CLICK_FISCAL_SPIC="10305001001000000",
    CLICK_FISCAL_PACKAGE_CODE="1501516",
    CLICK_FISCAL_TIN="123456789",
    CLICK_FISCAL_VAT_PERCENT=12,
)
class FiscalTests(ClickTestCase):
    def setUp(self):
        super().setUp()
        self.payment.status = ClickPayment.Status.PAID
        self.payment.click_trans_id = 9001
        self.payment.save()

    def test_payload_is_in_tiyin(self):
        payload = click.fiscal_payload(self.payment)
        item = payload["items"][0]

        self.assertEqual(payload["service_id"], 111)
        self.assertEqual(payload["payment_id"], 9001)
        self.assertEqual(payload["received_ecash"], 54_900_000)
        self.assertEqual(item["Price"], 54_900_000)
        self.assertEqual(item["SPIC"], "10305001001000000")
        self.assertEqual(item["CommissionInfo"], {"TIN": "123456789"})
        # 12% QQS summa ichida: 54 900 000 × 12 / 112.
        self.assertEqual(item["VAT"], 5_882_143)

    def test_auth_header(self):
        with patch("menu.click.timezone.now") as now:
            now.return_value.timestamp.return_value = 1_700_000_000
            header = click.merchant_auth_header()
        digest = hashlib.sha1(b"1700000000maxfiy").hexdigest()
        self.assertEqual(header, f"333:{digest}:1700000000")

    def test_success_marks_the_payment(self):
        response = MagicMock(status_code=200, content=b"{}")
        response.json.return_value = {"error_code": 0}
        with patch("requests.post", return_value=response) as post:
            submit_click_fiscal.delay(self.payment.pk)

        self.assertEqual(post.call_args.kwargs["json"]["payment_id"], 9001)
        self.payment.refresh_from_db()
        self.assertIsNotNone(self.payment.fiscalized_at)
        self.assertEqual(self.payment.fiscal_error, "")

    def test_error_is_kept_and_payment_stays_paid(self):
        response = MagicMock(status_code=200, content=b"{}")
        response.json.return_value = {"error_code": -5, "error_note": "SPIC not found"}
        with patch("requests.post", return_value=response), patch.object(
            submit_click_fiscal, "retry", side_effect=RuntimeError("retry")
        ):
            submit_click_fiscal.delay(self.payment.pk)

        self.payment.refresh_from_db()
        self.assertIsNone(self.payment.fiscalized_at)
        self.assertIn("SPIC not found", self.payment.fiscal_error)
        self.assertEqual(self.payment.status, ClickPayment.Status.PAID)


class NotifyTests(ClickTestCase):
    def test_message_names_the_restaurant(self):
        bot = FakeBot()
        with patch("menu.tasks.get_bot", return_value=bot):
            notify_click_payment(self.payment.pk)
        self.assertIn("Zamin", bot.sent[0]["text"])
        self.assertIn("6 oylik", bot.sent[0]["text"])
