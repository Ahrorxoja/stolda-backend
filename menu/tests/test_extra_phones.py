"""Qo'shimcha telefon raqamlari."""

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from menu.phones import (
    MAX_EXTRA_PHONES,
    clean_phone_list,
    is_valid_phone,
    validate_phone_list,
)

from .factories import make_restaurant

NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


class PhoneListTests(TestCase):
    def test_numbers_are_brought_to_one_shape(self):
        self.assertEqual(
            clean_phone_list(["+998 90 111 22 33", "901112234"]),
            ["+998901112233", "+998901112234"],
        )

    def test_blanks_and_duplicates_drop_out(self):
        self.assertEqual(
            clean_phone_list(["", "  ", "901112233", "+998 90 111 22 33"]),
            ["+998901112233"],
        )

    def test_primary_number_is_not_repeated(self):
        self.assertEqual(
            clean_phone_list(["901112233", "901112234"], exclude="+998 90 111 22 33"),
            ["+998901112234"],
        )

    def test_cleaning_keeps_everything_and_the_cap_is_a_separate_rule(self):
        """Tozalash qirqmaydi — cheklovni tekshiruvchi hal qiladi."""
        many = [f"9011122{n:02d}" for n in range(MAX_EXTRA_PHONES + 2)]

        self.assertEqual(len(clean_phone_list(many)), MAX_EXTRA_PHONES + 2)
        with self.assertRaises(ValidationError):
            validate_phone_list(many)

    def test_validator_rejects_rubbish(self):
        for bad in ("salom", 42, "", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    validate_phone_list([bad])

    def test_validator_rejects_a_long_list(self):
        with self.assertRaises(ValidationError):
            validate_phone_list([f"9011122{n:02d}" for n in range(MAX_EXTRA_PHONES + 1)])


class AdminExtraPhoneTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def test_owner_saves_extra_numbers(self):
        response = self.client.patch(
            self.url,
            {"extra_phones": ["+998 91 222 33 44", "935556677"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.restaurant.refresh_from_db()
        self.assertEqual(
            self.restaurant.extra_phones, ["+998912223344", "+998935556677"]
        )

    def test_empty_rows_are_ignored(self):
        response = self.client.patch(
            self.url, {"extra_phones": ["", "912223344", "   "]}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.extra_phones, ["+998912223344"])

    def test_primary_number_is_not_duplicated(self):
        self.client.patch(
            self.url,
            {"phone": "+998 90 123 45 67", "extra_phones": ["901234567", "912223344"]},
            format="json",
        )

        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.extra_phones, ["+998912223344"])

    def test_too_many_numbers_are_refused(self):
        response = self.client.patch(
            self.url,
            {"extra_phones": [f"9011122{n:02d}" for n in range(MAX_EXTRA_PHONES + 1)]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)


@NO_CACHE
class PublicExtraPhoneTests(TestCase):
    def test_menu_shows_every_number(self):
        restaurant = make_restaurant(extra_phones=["+998912223344"])

        body = self.client.get(reverse("public-menu", args=[restaurant.slug])).json()

        self.assertEqual(body["restaurant"]["phone"], "+998 90 123 45 67")
        self.assertEqual(body["restaurant"]["extra_phones"], ["+998912223344"])


class PhoneCompletenessTests(TestCase):
    """`normalize_phone` faqat ko'rinishni to'g'rilaydi — to'liqligi alohida."""

    def test_full_numbers_pass(self):
        for value in ("+998 90 123 45 67", "901234567", "998901234567"):
            with self.subTest(value=value):
                self.assertTrue(is_valid_phone(value))

    def test_short_or_broken_numbers_fail(self):
        for value in ("salom", "90123", "1", "", "  "):
            with self.subTest(value=value):
                self.assertFalse(is_valid_phone(value))


class RestaurantPhoneValidationTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def test_broken_phone_is_refused_instead_of_silently_emptied(self):
        response = self.client.patch(self.url, {"phone": "salom"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.phone, "+998 90 123 45 67")

    def test_half_typed_number_is_refused(self):
        response = self.client.patch(self.url, {"phone": "90123"}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_empty_phone_is_allowed(self):
        response = self.client.patch(self.url, {"phone": ""}, format="json")

        self.assertEqual(response.status_code, 200, response.data)

    def test_broken_extra_number_is_refused(self):
        response = self.client.patch(
            self.url, {"extra_phones": ["912223344", "90123"]}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.extra_phones, [])
