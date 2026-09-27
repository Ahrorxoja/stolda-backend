"""Restoranni o'chirish: faqat egasi, nomini tasdiqlab, fayllari bilan birga."""

import io
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from menu.models import (
    Category,
    Dish,
    DishPhoto,
    MenuView,
    PaymentReceipt,
    Restaurant,
    RestaurantMember,
    Subscription,
    ViewKind,
)

from .factories import make_category, make_dish, make_restaurant

MEDIA = tempfile.mkdtemp()


def image(name: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="WEBP")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/webp")


@override_settings(MEDIA_ROOT=MEDIA)
class DeleteRestaurantTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(logo=image("logo.webp"))
        self.owner = self.restaurant.owner
        self.category = make_category(self.restaurant)
        self.dish = make_dish(self.category)
        self.photo = DishPhoto.objects.create(dish=self.dish, image=image("osh.webp"))
        self.receipt = PaymentReceipt.objects.create(
            subscription=self.restaurant.subscription,
            image=image("chek.webp"),
            amount=99_000,
            period="month",
        )
        MenuView.objects.create(restaurant=self.restaurant, kind=ViewKind.SCAN)
        self.manager = get_user_model().objects.create_user(
            username="menejer", password="parol12345"
        )
        RestaurantMember.objects.create(
            restaurant=self.restaurant, user=self.manager, role=RestaurantMember.Role.MANAGER
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def test_summary_lists_what_will_be_lost(self):
        response = self.client.get(f"{self.url}deletion/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["slug"], "zamin")
        self.assertEqual(response.data["categories"], 1)
        self.assertEqual(response.data["dishes"], 1)
        self.assertEqual(response.data["photos"], 2)  # logo + taom rasmi
        self.assertEqual(response.data["views"], 1)
        self.assertEqual(response.data["managers"], 1)
        self.assertEqual(response.data["receipts"], 1)
        self.assertEqual(response.data["subscription_status"], "active")
        self.assertGreaterEqual(response.data["days_left"], 29)

    def test_owner_deletes_everything_including_files(self):
        paths = [
            self.restaurant.logo.path,
            self.photo.image.path,
            self.receipt.image.path,
        ]
        for path in paths:
            self.assertTrue(os.path.exists(path))

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url, {"confirm": "zamin"}, format="json")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Restaurant.objects.exists())
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Dish.objects.exists())
        self.assertFalse(Subscription.objects.exists())
        self.assertFalse(PaymentReceipt.objects.exists())
        self.assertFalse(MenuView.objects.exists())
        self.assertFalse(RestaurantMember.objects.exists())
        for path in paths:
            self.assertFalse(os.path.exists(path), path)
        # Hisobning o'zi qoladi — yangi restoran ochishi mumkin.
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())

    def test_confirmation_must_match_the_slug(self):
        for confirm in ("", "boshqa", None):
            response = self.client.delete(self.url, {"confirm": confirm}, format="json")
            self.assertEqual(response.status_code, 400)
        self.assertTrue(Restaurant.objects.exists())

    def test_confirmation_ignores_case_and_spaces(self):
        response = self.client.delete(self.url, {"confirm": "  Zamin "}, format="json")
        self.assertEqual(response.status_code, 204)

    def test_manager_cannot_delete_or_see_summary(self):
        self.client.force_authenticate(self.manager)

        self.assertEqual(self.client.get(f"{self.url}deletion/").status_code, 403)
        response = self.client.delete(self.url, {"confirm": "zamin"}, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Restaurant.objects.exists())

    def test_stranger_cannot_delete(self):
        stranger = get_user_model().objects.create_user(username="begona", password="x" * 10)
        self.client.force_authenticate(stranger)

        response = self.client.delete(self.url, {"confirm": "zamin"}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Restaurant.objects.exists())

    def test_public_menu_is_gone_after_deletion(self):
        self.client.delete(self.url, {"confirm": "zamin"}, format="json")

        self.assertEqual(APIClient().get("/api/public/zamin/menu/").status_code, 404)
