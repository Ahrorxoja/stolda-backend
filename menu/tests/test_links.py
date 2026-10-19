from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from menu.models import Restaurant, RestaurantLink

User = get_user_model()

class RestaurantLinkTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="testowner", password="password123")
        self.client.force_authenticate(user=self.owner)
        self.restaurant = Restaurant.objects.create(
            owner=self.owner,
            slug="test-res",
            name="Test Restaurant",
            primary_language="uz",
            languages=["uz", "ru"],
            is_active=True,
        )

    def test_create_and_list_links(self):
        response = self.client.post(
            "/api/links/",
            {
                "restaurant": self.restaurant.id,
                "title": {"uz": "Yandex Xarita"},
                "url": "https://yandex.uz/maps/1",
                "icon": "map-pin",
                "position": 0,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        menu_res = self.client.get(f"/api/public/{self.restaurant.slug}/menu/")
        self.assertEqual(menu_res.status_code, 200)
        self.assertEqual(len(menu_res.data["restaurant"]["custom_links"]), 1)
        self.assertEqual(menu_res.data["restaurant"]["custom_links"][0]["url"], "https://yandex.uz/maps/1")
