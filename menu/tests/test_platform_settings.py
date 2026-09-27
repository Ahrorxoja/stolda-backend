"""Karta va yordam aloqalari Django admindan o'zgartiriladi."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from menu.models import PlatformSettings

from .factories import make_restaurant


class PlatformSettingsTests(TestCase):
    def test_only_one_row_exists(self):
        PlatformSettings.objects.create(card_number="1111")
        PlatformSettings.objects.create(card_number="2222")

        self.assertEqual(PlatformSettings.objects.count(), 1)
        self.assertEqual(PlatformSettings.load().card_number, "2222")

    def test_load_creates_the_row_when_missing(self):
        PlatformSettings.objects.all().delete()

        self.assertEqual(PlatformSettings.load().pk, 1)


class BillingUsesPlatformSettingsTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)

    def test_card_from_admin_reaches_the_billing_page(self):
        platform = PlatformSettings.load()
        platform.card_number = "5614 6873 0518 7321"
        platform.card_holder = "Ahrorxoja Usmonxojayev"
        platform.support_phone = "+998933231320"
        platform.support_telegram = "@aha_daragoy"
        platform.save()

        payment = self.client.get("/api/billing/").data["payment"]

        self.assertEqual(payment["card_number"], "5614 6873 0518 7321")
        self.assertEqual(payment["card_holder"], "Ahrorxoja Usmonxojayev")
        self.assertEqual(payment["support_phone"], "+998933231320")
        self.assertEqual(payment["support_telegram"], "@aha_daragoy")

    @override_settings(PAYMENT_CARD_NUMBER="8600 0000 0000 0000", PAYMENT_CARD_HOLDER="ENV EGASI")
    def test_empty_admin_field_falls_back_to_env(self):
        """Eski o'rnatmalar buzilmasin: admin bo'sh bo'lsa `.env` ishlaydi."""
        PlatformSettings.objects.all().delete()

        payment = self.client.get("/api/billing/").data["payment"]

        self.assertEqual(payment["card_number"], "8600 0000 0000 0000")
        self.assertEqual(payment["card_holder"], "ENV EGASI")

    def test_admin_value_wins_over_env(self):
        with self.settings(PAYMENT_CARD_NUMBER="8600 0000 0000 0000"):
            platform = PlatformSettings.load()
            platform.card_number = "5614 6873 0518 7321"
            platform.save()

            payment = self.client.get("/api/billing/").data["payment"]

        self.assertEqual(payment["card_number"], "5614 6873 0518 7321")
