"""Hisob sozlamalari — platforma egasi bog'lanishi uchun aloqa ma'lumotlari."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from menu.models import Profile

from .factories import make_restaurant


class ProfileCreationTests(TestCase):
    def test_every_new_account_gets_a_profile(self):
        user = get_user_model().objects.create_user(username="yangi@gmail.com")

        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_telegram_url_is_built_from_the_name(self):
        user = get_user_model().objects.create_user(username="a@gmail.com")
        profile = user.profile
        profile.telegram = "@aziz"
        self.assertEqual(profile.telegram_url, "https://t.me/aziz")

    def test_empty_telegram_gives_no_link(self):
        user = get_user_model().objects.create_user(username="b@gmail.com")
        self.assertEqual(user.profile.telegram_url, "")


class ProfileApiTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)

    def test_me_returns_the_profile(self):
        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("profile", response.data)
        self.assertEqual(
            set(response.data["profile"]), {"full_name", "contact_phone", "telegram"}
        )

    def test_owner_saves_contact_details(self):
        response = self.client.patch(
            "/api/me/",
            {
                "full_name": "Aziz Rahimov",
                "contact_phone": "+998 90 111 22 33",
                "telegram": "aziz_rahimov",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        profile = self.restaurant.owner.profile
        profile.refresh_from_db()
        self.assertEqual(profile.full_name, "Aziz Rahimov")
        self.assertEqual(profile.contact_phone, "+998901112233")
        self.assertEqual(profile.telegram, "@aziz_rahimov")

    def test_telegram_link_is_reduced_to_a_name(self):
        # Telegram nomi kamida 5 ta belgidan iborat bo'ladi.
        for value in (
            "https://t.me/aziz_r",
            "t.me/aziz_r",
            "@aziz_r",
            "aziz_r",
        ):
            with self.subTest(value=value):
                response = self.client.patch(
                    "/api/me/", {"telegram": value}, format="json"
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["telegram"], "@aziz_r")

    def test_broken_phone_is_refused(self):
        response = self.client.patch(
            "/api/me/", {"contact_phone": "salom"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_blank_values_are_allowed(self):
        response = self.client.patch(
            "/api/me/", {"contact_phone": "", "telegram": ""}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["contact_phone"], "")

    def test_name_from_the_profile_is_shown_in_me(self):
        self.client.patch("/api/me/", {"full_name": "Dilshod Aka"}, format="json")

        response = self.client.get("/api/me/")

        self.assertEqual(response.data["name"], "Dilshod Aka")

    def test_signing_out_is_not_needed_to_see_changes(self):
        """`/api/me/` har safar bazadan o'qiydi — keshlanmaydi."""
        self.client.patch("/api/me/", {"telegram": "@yangi"}, format="json")

        self.assertEqual(
            self.client.get("/api/me/").data["profile"]["telegram"], "@yangi"
        )

    def test_another_user_cannot_change_your_profile(self):
        stranger = get_user_model().objects.create_user(username="begona@gmail.com")
        other = APIClient()
        other.force_authenticate(stranger)

        other.patch("/api/me/", {"full_name": "Boshqa"}, format="json")

        self.restaurant.owner.profile.refresh_from_db()
        self.assertNotEqual(self.restaurant.owner.profile.full_name, "Boshqa")
        self.assertEqual(Profile.objects.get(user=stranger).full_name, "Boshqa")

    def test_half_typed_contact_phone_is_refused(self):
        response = self.client.patch(
            "/api/me/", {"contact_phone": "90123"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_broken_telegram_name_is_refused(self):
        for value in ("ab", "@nom!!", "aziz", "a b c d e"):
            with self.subTest(value=value):
                response = self.client.patch(
                    "/api/me/", {"telegram": value}, format="json"
                )
                self.assertEqual(response.status_code, 400, value)
