"""Google bilan kirish va restoransiz foydalanuvchining ro'yxatdan o'tish oqimi."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from menu.models import Restaurant

from .factories import make_restaurant

PASSWORD = "parol12345"


def auth_header(user) -> dict:
    token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class SlugCheckTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(slug="zamin")
        self.user = get_user_model().objects.create_user(username="tekshiruvchi")

    def test_free_slug_is_available(self):
        response = self.client.get(
            reverse("restaurant-slug-check"), {"slug": "yangi-oshxona"}, **auth_header(self.user)
        )
        self.assertEqual(response.json(), {"slug": "yangi-oshxona", "available": True})

    def test_taken_slug_is_not_available(self):
        response = self.client.get(
            reverse("restaurant-slug-check"), {"slug": "Zamin"}, **auth_header(self.user)
        )
        self.assertEqual(response.json(), {"slug": "zamin", "available": False})

    def test_reserved_slug_is_not_available(self):
        response = self.client.get(
            reverse("restaurant-slug-check"), {"slug": "admin"}, **auth_header(self.user)
        )
        self.assertFalse(response.json()["available"])

    def test_requires_auth(self):
        response = self.client.get(reverse("restaurant-slug-check"), {"slug": "bosh"})
        self.assertEqual(response.status_code, 401)


class RestaurantOnboardingFlowTests(TestCase):
    """Google orqali kirgan, hali restorani yo'q foydalanuvchi."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="egasiz@example.com")

    def test_create_then_patch_languages(self):
        create = self.client.post(
            reverse("restaurant-list"),
            {"name": "Bahor", "slug": "bahor", "phone": "+998901234567", "city": "Toshkent"},
            content_type="application/json",
            **auth_header(self.user),
        )
        self.assertEqual(create.status_code, 201, create.content)
        restaurant_id = create.json()["id"]
        self.assertEqual(Restaurant.objects.get(pk=restaurant_id).owner, self.user)

        patch = self.client.patch(
            reverse("restaurant-detail", args=[restaurant_id]),
            {"languages": ["uz", "ru"], "primary_language": "uz"},
            content_type="application/json",
            **auth_header(self.user),
        )
        self.assertEqual(patch.status_code, 200, patch.content)

        restaurant = Restaurant.objects.get(pk=restaurant_id)
        self.assertEqual(restaurant.languages, ["uz", "ru"])
        self.assertEqual(restaurant.primary_language, "uz")

    def test_duplicate_slug_is_rejected(self):
        make_restaurant(slug="band")
        response = self.client.post(
            reverse("restaurant-list"),
            {"name": "Ikkinchi", "slug": "band"},
            content_type="application/json",
            **auth_header(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("slug", response.json())

    def test_reserved_slug_is_rejected(self):
        response = self.client.post(
            reverse("restaurant-list"),
            {"name": "Admin", "slug": "admin"},
            content_type="application/json",
            **auth_header(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("slug", response.json())


class GoogleAuthTests(TestCase):
    def _post(self, credential="fake-token"):
        return self.client.post(
            reverse("google-auth"),
            {"credential": credential},
            content_type="application/json",
        )

    @override_settings(GOOGLE_CLIENT_ID="test-client-id")
    @patch("menu.auth.google_id_token.verify_oauth2_token")
    def test_creates_user_on_first_login(self, verify):
        verify.return_value = {"email": "Chef@Example.com", "email_verified": True}
        response = self._post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.json())
        self.assertTrue(
            get_user_model().objects.filter(username="chef@example.com").exists()
        )

    @override_settings(GOOGLE_CLIENT_ID="test-client-id")
    @patch("menu.auth.google_id_token.verify_oauth2_token")
    def test_second_login_reuses_the_same_user(self, verify):
        verify.return_value = {"email": "chef@example.com", "email_verified": True}
        self._post()
        self._post()
        self.assertEqual(
            get_user_model().objects.filter(username="chef@example.com").count(), 1
        )

    @override_settings(GOOGLE_CLIENT_ID="test-client-id")
    @patch("menu.auth.google_id_token.verify_oauth2_token")
    def test_unverified_email_is_rejected(self, verify):
        verify.return_value = {"email": "chef@example.com", "email_verified": False}
        response = self._post()
        self.assertEqual(response.status_code, 400)

    @override_settings(GOOGLE_CLIENT_ID="test-client-id")
    @patch("menu.auth.google_id_token.verify_oauth2_token")
    def test_invalid_token_is_rejected(self, verify):
        verify.side_effect = ValueError("token muddati o'tgan")
        response = self._post()
        self.assertEqual(response.status_code, 400)

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_disabled_without_client_id(self):
        response = self._post()
        self.assertEqual(response.status_code, 400)


class RestaurantQrTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(slug="zamin")
        self.owner = self.restaurant.owner
        self.other = get_user_model().objects.create_user(username="boshqa")

    def qr(self, fmt: str, user=None):
        return self.client.get(
            reverse("restaurant-qr", args=[self.restaurant.pk]),
            {"format": fmt},
            **auth_header(user or self.owner),
        )

    def test_png(self):
        response = self.qr("png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_a6_pdf(self):
        response = self.qr("a6")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_a4_pdf(self):
        response = self.qr("a4")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_unknown_format_rejected(self):
        response = self.qr("svg")
        self.assertEqual(response.status_code, 400)

    def test_other_users_cannot_fetch_it(self):
        response = self.qr("png", user=self.other)
        self.assertEqual(response.status_code, 404)
