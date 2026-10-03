"""Yetkazib berish raqamlari va ularga qo'ng'iroq statistikasi."""

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from menu.models import MenuView, ViewKind
from menu.phones import MAX_EXTRA_PHONES

from .factories import make_restaurant

NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


class AdminDeliveryPhoneTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def test_owner_saves_numbers_in_one_shape(self):
        response = self.client.patch(
            self.url,
            {"delivery_phones": ["+998 91 222 33 44", "", "935556677", "912223344"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.restaurant.refresh_from_db()
        # Bo'sh qator tushdi, takror bittaga qisqardi.
        self.assertEqual(
            self.restaurant.delivery_phones, ["+998912223344", "+998935556677"]
        )
        self.assertEqual(response.data["delivery_phones"], ["+998912223344", "+998935556677"])

    def test_the_main_number_may_also_be_the_delivery_number(self):
        """Ko'p kafeda yetkazib berish shu raqamning o'zida — taqiqlanmaydi."""
        response = self.client.patch(
            self.url, {"delivery_phones": ["+998 90 123 45 67"]}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.delivery_phones, ["+998901234567"])

    def test_broken_number_is_refused(self):
        response = self.client.patch(self.url, {"delivery_phones": ["90123"]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_too_many_numbers_are_refused(self):
        response = self.client.patch(
            self.url,
            {"delivery_phones": [f"9011122{n:02d}" for n in range(MAX_EXTRA_PHONES + 1)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_clearing_removes_delivery(self):
        self.restaurant.delivery_phones = ["+998912223344"]
        self.restaurant.save()

        self.client.patch(self.url, {"delivery_phones": []}, format="json")

        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.delivery_phones, [])


@NO_CACHE
class PublicDeliveryTests(TestCase):
    def test_menu_lists_delivery_numbers(self):
        restaurant = make_restaurant(delivery_phones=["+998912223344"])

        body = self.client.get(reverse("public-menu", args=[restaurant.slug])).json()

        self.assertEqual(body["restaurant"]["delivery_phones"], ["+998912223344"])

    def test_menu_without_delivery_has_an_empty_list(self):
        restaurant = make_restaurant()

        body = self.client.get(reverse("public-menu", args=[restaurant.slug])).json()

        self.assertEqual(body["restaurant"]["delivery_phones"], [])


class DeliveryCallStatsTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(delivery_phones=["+998912223344"])
        self.views_url = reverse("public-views", args=[self.restaurant.slug])

    def call(self, restaurant=None):
        url = reverse("public-views", args=[(restaurant or self.restaurant).slug])
        return self.client.post(url, {"kind": "delivery_call"}, content_type="application/json")

    def test_call_is_recorded_anonymously(self):
        response = self.call()

        self.assertEqual(response.status_code, 204)
        view = MenuView.objects.get()
        self.assertEqual(view.kind, ViewKind.DELIVERY_CALL)
        self.assertIsNone(view.dish)

    def test_call_without_delivery_numbers_is_refused(self):
        other = make_restaurant(slug="yetkazmaydi")

        self.assertEqual(self.call(other).status_code, 400)
        self.assertFalse(MenuView.objects.exists())

    def test_stats_count_calls_separately(self):
        self.call()
        self.call()
        self.client.post(self.views_url, {"kind": "scan"}, content_type="application/json")

        api = APIClient()
        api.force_authenticate(self.restaurant.owner)
        body = api.get(reverse("stats") + "?range=week").json()

        self.assertEqual(body["today"]["delivery_calls"], 2)
        self.assertEqual(body["period"]["delivery_calls"], 2)
        # Qo'ng'iroqlar skaner yoki taom ochilishiga qo'shilib ketmaydi.
        self.assertEqual(body["today"]["scans"], 1)
        self.assertEqual(body["today"]["dish_opens"], 0)
