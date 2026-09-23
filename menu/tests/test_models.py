from django.core.exceptions import ValidationError
from django.test import TestCase

from menu.models import Table
from menu.translations import translate, validate_translation, validate_translation_list

from .factories import make_category, make_dish, make_plan, make_restaurant


class TranslationTests(TestCase):
    def test_picks_requested_language(self):
        value = {"uz": "Salatlar", "ru": "Салаты", "en": "Salads"}
        self.assertEqual(translate(value, "ru"), "Салаты")

    def test_falls_back_to_uz_when_language_is_empty(self):
        value = {"uz": "Salatlar", "ru": "", "en": "Salads"}
        self.assertEqual(translate(value, "ru"), "Salatlar")

    def test_falls_back_to_uz_when_language_is_missing(self):
        self.assertEqual(translate({"uz": "Salatlar"}, "en"), "Salatlar")

    def test_rejects_unknown_language(self):
        with self.assertRaises(ValidationError):
            validate_translation({"uz": "Salat", "xx": "Salata"})

    def test_accepts_every_supported_language(self):
        validate_translation({"uz": "Salat", "tr": "Salata", "ar": "سلطة"})

    def test_rejects_non_string_value(self):
        with self.assertRaises(ValidationError):
            validate_translation({"uz": 42})

    def test_ingredient_lists_must_be_strings(self):
        validate_translation_list({"uz": ["Tovuq"], "ru": [], "en": []})
        with self.assertRaises(ValidationError):
            validate_translation_list({"uz": [42]})


class DishTests(TestCase):
    def setUp(self):
        self.category = make_category(make_restaurant())

    def test_accepts_known_badges(self):
        dish = make_dish(self.category, badges=["popular", "veg"])
        dish.full_clean()

    def test_rejects_unknown_badge(self):
        dish = make_dish(self.category, badges=["spicy"])
        with self.assertRaises(ValidationError):
            dish.full_clean()


class PlanLimitTests(TestCase):
    def test_limited_plan_stops_at_its_dish_limit(self):
        limited = make_plan(code="limited", features={"dish_limit": 30, "stats": False})
        restaurant = make_restaurant(plan=limited)
        category = make_category(restaurant)
        for _ in range(30):
            make_dish(category)

        self.assertEqual(restaurant.dish_count, 30)
        self.assertFalse(restaurant.can_add_dish())

    def test_standard_plan_is_unlimited(self):
        restaurant = make_restaurant()
        category = make_category(restaurant)
        for _ in range(31):
            make_dish(category)

        self.assertTrue(restaurant.can_add_dish())
        self.assertTrue(restaurant.limits["stats"])

    def test_limited_plan_can_have_no_stats(self):
        limited = make_plan(code="limited", features={"dish_limit": None, "stats": False})
        self.assertFalse(make_restaurant(plan=limited).limits["stats"])

    def test_restaurant_without_subscription_gets_the_most_restrictive_limits(self):
        restaurant = make_restaurant(subscription_status=None)
        self.assertFalse(restaurant.can_add_dish())
        self.assertFalse(restaurant.limits["stats"])


class TableTests(TestCase):
    def test_qr_token_is_generated_and_unique(self):
        restaurant = make_restaurant()
        first = Table.objects.create(restaurant=restaurant, number="1")
        second = Table.objects.create(restaurant=restaurant, number="2")

        self.assertTrue(first.qr_token)
        self.assertNotEqual(first.qr_token, second.qr_token)

    def test_qr_url_points_at_the_restaurant_slug(self):
        table = Table.objects.create(restaurant=make_restaurant(), number="7")
        self.assertEqual(
            table.qr_url, f"https://stolda.uz/zamin?t={table.qr_token}"
        )
