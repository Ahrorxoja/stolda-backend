from django.test import TestCase, override_settings
from django.urls import reverse

from menu.models import MenuView, Table, ViewKind

from .factories import make_category, make_dish, make_restaurant

# `cache_page(60)` testlar orasida javobni saqlab qolmasligi uchun.
NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


@NO_CACHE
class PublicMenuTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.category = make_category(self.restaurant, position=0)
        self.dish = make_dish(self.category, badges=["popular"])
        self.url = reverse("public-menu", args=[self.restaurant.slug])

    def test_returns_restaurant_categories_and_dishes(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["restaurant"]["slug"], "zamin")
        self.assertEqual(body["restaurant"]["service_charge_percent"], 20)
        self.assertEqual(len(body["categories"]), 1)
        self.assertEqual(len(body["dishes"]), 1)
        self.assertEqual(body["dishes"][0]["category"], self.category.pk)

    def test_returns_all_three_languages_in_one_response(self):
        body = self.client.get(self.url).json()

        self.assertEqual(
            body["categories"][0]["name"],
            {"uz": "Salatlar", "ru": "Салаты", "en": "Salads"},
        )
        self.assertEqual(body["dishes"][0]["ingredients"]["uz"], ["Tovuq", "Parmezan"])

    def test_hidden_dishes_are_left_out(self):
        make_dish(self.category, is_available=False)

        body = self.client.get(self.url).json()

        self.assertEqual(len(body["dishes"]), 1)
        self.assertEqual(body["categories"][0]["dish_count"], 1)

    def test_dishes_follow_category_and_position_order(self):
        second = make_category(self.restaurant, position=1)
        make_dish(second, position=0, price=10000)
        make_dish(self.category, position=1, price=20000)

        prices = [dish["price"] for dish in self.client.get(self.url).json()["dishes"]]

        self.assertEqual(prices, [45000, 20000, 10000])

    def test_other_restaurants_are_not_mixed_in(self):
        other = make_restaurant(slug="boshqa", name="Boshqa")
        make_dish(make_category(other))

        body = self.client.get(self.url).json()

        self.assertEqual(len(body["dishes"]), 1)

    def test_unknown_slug_is_not_found(self):
        self.assertEqual(self.client.get(reverse("public-menu", args=["yoq"])).status_code, 404)

    def test_inactive_restaurant_is_not_found(self):
        self.restaurant.is_active = False
        self.restaurant.save(update_fields=["is_active"])

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_menu_is_served_in_a_handful_of_queries(self):
        for _ in range(5):
            make_dish(self.category)

        # restoran, vaqt jadvali bayrog'i, kategoriyalar, taomlar, taom rasmlari.
        # Bayroq odatda keshdan olinadi (bu testda kesh o'chirilgan).
        with self.assertNumQueries(5):
            self.client.get(self.url)


@NO_CACHE
class PublicViewEventTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.category = make_category(self.restaurant)
        self.dish = make_dish(self.category)
        self.table = Table.objects.create(restaurant=self.restaurant, number="3")
        self.url = reverse("public-views", args=[self.restaurant.slug])

    def post(self, payload):
        return self.client.post(self.url, payload, content_type="application/json")

    def test_records_a_scan_with_its_table(self):
        response = self.post({"kind": "scan", "table": self.table.qr_token})

        self.assertEqual(response.status_code, 204)
        event = MenuView.objects.get()
        self.assertEqual(event.kind, ViewKind.SCAN)
        self.assertEqual(event.table, self.table)
        self.assertIsNone(event.dish)

    def test_records_a_dish_open(self):
        self.post({"kind": "dish_open", "dish": self.dish.pk})

        event = MenuView.objects.get()
        self.assertEqual(event.kind, ViewKind.DISH_OPEN)
        self.assertEqual(event.dish, self.dish)

    def test_dish_open_requires_a_dish(self):
        response = self.post({"kind": "dish_open"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MenuView.objects.exists())

    def test_dish_from_another_restaurant_is_rejected(self):
        other_dish = make_dish(make_category(make_restaurant(slug="boshqa", name="Boshqa")))

        response = self.post({"kind": "dish_open", "dish": other_dish.pk})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MenuView.objects.exists())

    def test_unknown_kind_is_rejected(self):
        self.assertEqual(self.post({"kind": "order"}).status_code, 400)

    def test_unknown_table_token_still_records_the_scan(self):
        response = self.post({"kind": "scan", "table": "yoq-bunday-token"})

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(MenuView.objects.get().table)

    def test_no_authentication_required(self):
        self.assertEqual(self.post({"kind": "scan"}).status_code, 204)
