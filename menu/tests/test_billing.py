"""7-bosqich: obuna hayot sikli, to'lov provayderlari, webhook'lar."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from menu.models import Invoice, PaymentMethod, Plan, Restaurant, Subscription
from menu.tasks import process_subscriptions

from .factories import make_restaurant


def auth_header(user) -> dict:
    token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class TrialAbuseTests(TestCase):
    """Sinov faqat bir marta — telefon raqami bo'yicha."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="birinchi@example.com")

    def _create(self, user, **overrides):
        body = {"name": "Bahor", "slug": "bahor", "phone": "+998901234567"}
        body.update(overrides)
        return self.client.post(
            reverse("restaurant-list"),
            body,
            content_type="application/json",
            **auth_header(user),
        )

    def test_first_restaurant_gets_a_trial(self):
        response = self._create(self.user)
        self.assertEqual(response.status_code, 201, response.content)

        restaurant = Restaurant.objects.get(pk=response.json()["id"])
        self.assertIsNotNone(restaurant.trial_used_at)
        self.assertEqual(restaurant.subscription.status, Subscription.Status.TRIALING)
        self.assertIsNotNone(restaurant.subscription.trial_ends_at)
        self.assertEqual(restaurant.subscription.plan.code, "standard")

    def test_same_phone_cannot_get_a_second_trial(self):
        self._create(self.user)
        second_user = get_user_model().objects.create_user(username="ikkinchi@example.com")

        # Turli formatda yozilgan, lekin xuddi shu raqam.
        response = self._create(
            second_user, slug="yana-bittasi", name="Yana bittasi", phone="998 90 123 45 67"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.json())
        self.assertFalse(Restaurant.objects.filter(slug="yana-bittasi").exists())

    def test_different_phone_gets_its_own_trial(self):
        self._create(self.user)
        second_user = get_user_model().objects.create_user(username="ikkinchi@example.com")

        response = self._create(
            second_user, slug="boshqa", name="Boshqa", phone="+998907654321"
        )

        self.assertEqual(response.status_code, 201, response.content)


class ProcessSubscriptionsTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.ACTIVE)
        self.subscription = self.restaurant.subscription

    def test_unpaid_renewal_becomes_past_due(self):
        self.subscription.current_period_end = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["current_period_end"])

        process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PAST_DUE)
        self.assertIsNotNone(self.subscription.grace_ends_at)

    def test_past_due_still_serves_the_full_menu(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.grace_ends_at = timezone.now() + timedelta(days=3)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        response = self.client.get(reverse("public-menu", args=[self.restaurant.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("active", response.json())

    def test_grace_period_expiry_suspends(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.grace_ends_at = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.SUSPENDED)

    def test_past_due_recovers_on_a_retry_day_with_a_working_card(self):
        PaymentMethod.objects.create(
            restaurant=self.restaurant,
            provider="payme",
            token="tok_ok",
            last4="1234",
            exp_month=12,
            exp_year=2030,
            is_default=True,
        )
        self.subscription.status = Subscription.Status.PAST_DUE
        # `grace_ends_at` bugundan 6 kun keyin => imtiyozli muddat 1 kun oldin
        # boshlangan => 1-kunlik qayta urinish oynasiga tushadi.
        self.subscription.grace_ends_at = timezone.now() + timedelta(days=6)
        self.subscription.save(update_fields=["status", "grace_ends_at"])

        process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(Invoice.objects.filter(subscription=self.subscription).count(), 1)

    def test_canceled_subscription_skips_charge_and_still_suspends(self):
        PaymentMethod.objects.create(
            restaurant=self.restaurant,
            provider="payme",
            token="tok_ok",
            last4="1234",
            exp_month=12,
            exp_year=2030,
            is_default=True,
        )
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.canceled_at = timezone.now()
        self.subscription.grace_ends_at = timezone.now() - timedelta(hours=1)
        self.subscription.save(update_fields=["status", "canceled_at", "grace_ends_at"])

        process_subscriptions()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.SUSPENDED)
        self.assertEqual(Invoice.objects.count(), 0)


class PublicMenuGateTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.SUSPENDED)

    def test_suspended_menu_returns_the_minimal_inactive_payload(self):
        response = self.client.get(reverse("public-menu", args=[self.restaurant.slug]))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["active"])
        self.assertEqual(
            set(data["restaurant"]), {"name", "logo", "phone", "address", "instagram"}
        )

    def test_menu_is_restored_immediately_after_a_successful_webhook(self):
        before = self.client.get(reverse("public-menu", args=[self.restaurant.slug]))
        self.assertFalse(before.json()["active"])

        webhook = self.client.post(
            reverse("billing-webhook", args=["payme"]),
            data=json.dumps(
                {
                    "provider_payment_id": "restore_1",
                    "amount": 99_000,
                    "restaurant": self.restaurant.pk,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(webhook.status_code, 200, webhook.content)

        after = self.client.get(reverse("public-menu", args=[self.restaurant.slug]))
        # Full menyu javobida "active" kaliti yo'q — kesh 60s kutmadi.
        self.assertNotIn("active", after.json())

        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.subscription.status, Subscription.Status.ACTIVE)


class WebhookIdempotencyTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(subscription_status=Subscription.Status.ACTIVE)

    def _post_webhook(self, provider_payment_id: str):
        return self.client.post(
            reverse("billing-webhook", args=["click"]),
            data=json.dumps(
                {
                    "provider_payment_id": provider_payment_id,
                    "amount": 99_000,
                    "restaurant": self.restaurant.pk,
                }
            ),
            content_type="application/json",
        )

    def test_duplicate_webhook_creates_only_one_invoice(self):
        self._post_webhook("dup_1")
        self._post_webhook("dup_1")

        self.assertEqual(Invoice.objects.filter(provider_payment_id="dup_1").count(), 1)

    def test_duplicate_webhook_extends_the_period_only_once(self):
        self._post_webhook("dup_2")
        self.restaurant.refresh_from_db()
        first_end = self.restaurant.subscription.current_period_end

        self._post_webhook("dup_2")
        self.restaurant.refresh_from_db()

        self.assertEqual(self.restaurant.subscription.current_period_end, first_end)


class BillingApiTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.user = self.restaurant.owner

    def test_billing_overview_shape(self):
        response = self.client.get(reverse("billing"), **auth_header(self.user))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["subscription"]["status"], "active")
        self.assertIsNone(data["payment_method"])
        self.assertTrue(any(plan["code"] == "standard" for plan in data["plans"]))

    def test_subscribe_without_a_card_returns_a_checkout_url(self):
        response = self.client.post(
            reverse("billing-subscribe"),
            {"plan": "standard", "period": "month", "provider": "payme"},
            content_type="application/json",
            **auth_header(self.user),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("checkout_url", response.json())

    def test_card_then_subscribe_charges_immediately(self):
        self.client.post(
            reverse("billing-card"),
            {
                "provider": "payme",
                "token": "tok_ok",
                "brand": "UZCARD",
                "last4": "4242",
                "exp_month": 12,
                "exp_year": 2030,
            },
            content_type="application/json",
            **auth_header(self.user),
        )

        response = self.client.post(
            reverse("billing-subscribe"),
            {"plan": "standard", "period": "month", "provider": "payme"},
            content_type="application/json",
            **auth_header(self.user),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")
        self.assertEqual(Invoice.objects.filter(subscription=self.restaurant.subscription).count(), 1)

    def test_other_owners_cannot_fetch_a_foreign_receipt(self):
        invoice = Invoice.objects.create(
            subscription=self.restaurant.subscription,
            amount=99_000,
            period="month",
            period_start=timezone.now(),
            period_end=timezone.now() + timedelta(days=30),
            status=Invoice.Status.PAID,
            receipt_url="https://ofd.example/1",
        )
        other = get_user_model().objects.create_user(username="begona@example.com")

        response = self.client.get(
            reverse("billing-invoice-receipt", args=[invoice.pk]), **auth_header(other)
        )

        self.assertEqual(response.status_code, 404)
