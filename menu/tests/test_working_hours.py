"""Haftalik ish vaqti va ijtimoiy sahifalar."""

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from menu.hours import DAYS, day_hours, default_working_hours, parse_hours_text, validate_working_hours

from .factories import make_restaurant

NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


class WorkingHoursValidationTests(TestCase):
    def test_default_covers_every_day(self):
        hours = default_working_hours()
        self.assertEqual(set(hours), set(DAYS))
        self.assertFalse(hours["mon"]["closed"])

    def test_valid_schedule_passes(self):
        hours = default_working_hours()
        hours["sun"] = day_hours("11:00", "18:00", closed=True)
        validate_working_hours(hours)

    def test_unknown_day_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_working_hours({"funday": day_hours()})

    def test_broken_time_is_rejected(self):
        for bad in ("25:00", "9:00", "10-00", "", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    validate_working_hours({"mon": {"open": bad, "close": "22:00", "closed": False}})

    def test_closed_must_be_boolean(self):
        with self.assertRaises(ValidationError):
            validate_working_hours({"mon": {"open": "10:00", "close": "22:00", "closed": "ha"}})

    def test_old_text_is_parsed_for_migration(self):
        self.assertEqual(parse_hours_text("Har kuni 10:00 – 23:00")["mon"]["close"], "23:00")
        self.assertEqual(parse_hours_text("9:00-18:30")["sun"]["open"], "09:00")
        self.assertIsNone(parse_hours_text("doim ochiq"))


@NO_CACHE
class PublicHoursTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(
            instagram="zamin_resto",
            facebook="https://facebook.com/zaminresto",
            telegram="@zamin",
        )
        self.url = reverse("public-menu", args=[self.restaurant.slug])

    def test_menu_exposes_schedule_and_socials(self):
        body = self.client.get(self.url).json()

        self.assertEqual(set(body["restaurant"]["working_hours"]), set(DAYS))
        self.assertEqual(body["restaurant"]["instagram"], "zamin_resto")
        self.assertEqual(body["restaurant"]["facebook"], "https://facebook.com/zaminresto")
        self.assertEqual(body["restaurant"]["telegram"], "@zamin")

    def test_free_text_hours_field_is_gone(self):
        body = self.client.get(self.url).json()
        self.assertNotIn("hours", body["restaurant"])


class AdminHoursTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def test_owner_can_save_a_weekly_schedule(self):
        hours = default_working_hours()
        hours["sun"] = day_hours("10:00", "22:00", closed=True)
        hours["sat"] = day_hours("11:00", "23:30")

        response = self.client.patch(self.url, {"working_hours": hours}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.restaurant.refresh_from_db()
        self.assertTrue(self.restaurant.working_hours["sun"]["closed"])
        self.assertEqual(self.restaurant.working_hours["sat"]["close"], "23:30")

    def test_broken_schedule_is_rejected(self):
        response = self.client.patch(
            self.url,
            {"working_hours": {"mon": {"open": "31:00", "close": "22:00", "closed": False}}},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.working_hours["mon"]["open"], "10:00")

    def test_owner_can_save_social_pages(self):
        response = self.client.patch(
            self.url,
            {"instagram": "zamin_resto", "facebook": "zaminresto", "telegram": "zamin_bot"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.facebook, "zaminresto")
        self.assertEqual(self.restaurant.telegram, "zamin_bot")
