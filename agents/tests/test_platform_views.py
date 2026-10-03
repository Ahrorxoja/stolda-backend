"""Platforma paneli: agent sahifasi, faolsizlantirish, restoranni boshqa agentga o'tkazish."""

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from agents.models import AgentEarning, Place, Visit
from menu.tests.factories import make_restaurant

from .test_agents import AgentTestCase, make_agent


class PlatformAgentTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        owner = get_user_model().objects.create_user(username="ega@gmail.com", is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.ali = make_agent(name="Ali Valiyev", code="ALI", telegram_chat_id="11")
        self.jasur = make_agent(name="Jasur Karimov", code="JASUR", telegram_chat_id="12")
        self.restaurant = make_restaurant(slug="rayhon", name="Rayhon", phone="+998901112233")
        self.restaurant.agent = self.ali
        self.restaurant.save()
        AgentEarning.objects.create(
            agent=self.ali, restaurant=self.restaurant, restaurant_name="Rayhon", kind="first", amount=49_500
        )
        place = Place.objects.create(region="Samarqand", name="Registon", address="Markaz")
        Visit.objects.create(place=place, agent=self.ali, outcome="refused", comment="Qimmat dedi")

    def test_only_platform_owner(self):
        stranger = APIClient()
        stranger.force_authenticate(get_user_model().objects.create_user(username="egasi"))
        self.assertEqual(stranger.get(f"/api/platform/agents/{self.ali.pk}/").status_code, 403)
        self.assertEqual(stranger.get("/api/platform/restaurants/").status_code, 403)

    def test_agent_page_has_restaurants_visits_and_money(self):
        data = self.client.get(f"/api/platform/agents/{self.ali.pk}/").json()

        self.assertEqual((data["code"], data["status"]), ("ALI", "active"))
        self.assertEqual(data["restaurants"][0]["name"], "Rayhon")
        self.assertEqual(data["restaurants"][0]["earned"], 49_500)
        self.assertEqual(data["visits"][0]["comment"], "Qimmat dedi")
        self.assertEqual(len(data["months"]), 12)
        self.assertEqual(data["months"][-1]["amount"], 49_500)
        self.assertEqual(data["balance"]["available"], 49_500)

    def test_deactivate_and_reactivate_notifies_agent(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f"/api/platform/agents/{self.ali.pk}/active/", {"is_active": False}, format="json")
        self.assertFalse(response.json()["is_active"])
        self.ali.refresh_from_db()
        self.assertFalse(self.ali.is_active)
        self.assertIn("faolsizlantirildi", self.agent_bot.sent[-1]["text"])

    def test_search_shows_agent_and_reassign(self):
        rows = self.client.get("/api/platform/restaurants/", {"q": "rayh"}).json()
        self.assertEqual(rows[0]["agent"]["code"], "ALI")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/api/platform/restaurants/{self.restaurant.pk}/agent/", {"agent": self.jasur.pk}, format="json"
            )
        self.assertEqual(response.json()["agent"]["code"], "JASUR")
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.agent, self.jasur)
        texts = [item["text"] for item in self.agent_bot.sent]
        self.assertTrue(any("boshqa agentga o'tkazildi" in text for text in texts))
        self.assertTrue(any("sizga biriktirildi" in text for text in texts))

        self.client.post(f"/api/platform/restaurants/{self.restaurant.pk}/agent/", {"agent": None}, format="json")
        self.restaurant.refresh_from_db()
        self.assertIsNone(self.restaurant.agent)
