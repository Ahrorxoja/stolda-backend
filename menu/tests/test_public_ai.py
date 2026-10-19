from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from menu.models import Restaurant, Category, Dish

User = get_user_model()

class PublicAiAssistantTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="testowner")
        self.restaurant = Restaurant.objects.create(
            owner=self.owner,
            slug="test-res",
            name="Test Restaurant",
            primary_language="uz",
            languages=["uz", "ru"],
            is_active=True,
        )
        self.category = Category.objects.create(
            restaurant=self.restaurant,
            name={"uz": "Milliy taomlar"},
            is_visible=True,
        )
        self.dish = Dish.objects.create(
            category=self.category,
            name={"uz": "Palov"},
            price=35000,
            is_available=True,
        )

    def test_ai_assistant_endpoint(self):
        url = f"/api/public/{self.restaurant.slug}/ai-assistant/"
        response = self.client.post(
            url,
            {"message": "Nima tavsiya qilasiz?", "lang": "uz"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("reply", response.data)

    def test_ai_assistant_404_slug(self):
        url = "/api/public/nonexistent-res/ai-assistant/"
        response = self.client.post(
            url,
            {"message": "Salom"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
