"""Agentlar Mini App'i: Telegram imzosi, kirish qoidalari, jurnal, daromad."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from django.test import override_settings
from rest_framework.test import APIClient

from agents import webapp
from agents.models import AgentEarning, AgentWithdrawal, Place
from menu.models import Subscription
from menu.tests.factories import make_restaurant

from .test_agents import AgentTestCase, make_agent

TOKEN = "123:test-token"


def init_data(user_id, *, token=TOKEN, auth_date=None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": int(user_id), "first_name": "Ali"}),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class VerifyInitDataTests(AgentTestCase):
    def test_valid_signature_returns_user(self):
        self.assertEqual(webapp.verify_init_data(init_data(42), TOKEN)["id"], 42)

    def test_wrong_token_tampering_and_old_data_are_rejected(self):
        self.assertIsNone(webapp.verify_init_data(init_data(42, token="999:other"), TOKEN))
        self.assertIsNone(webapp.verify_init_data(init_data(42).replace("42", "43"), TOKEN))
        old = int(time.time()) - webapp.INIT_DATA_MAX_AGE - 10
        self.assertIsNone(webapp.verify_init_data(init_data(42, auth_date=old), TOKEN))
        self.assertIsNone(webapp.verify_init_data("", TOKEN))


@override_settings(AGENT_BOT_TOKEN=TOKEN)
class AgentAppTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.ali = make_agent(name="Ali Valiyev", code="ALI", telegram_chat_id="101")
        self.jasur = make_agent(name="Jasur Karimov", code="JASUR", telegram_chat_id="102")

    def client_for(self, agent) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_X_TELEGRAM_INIT_DATA=init_data(agent.telegram_chat_id))
        return client

    def test_me(self):
        data = self.client_for(self.ali).get("/api/agents/app/me/").json()

        self.assertEqual((data["first_name"], data["code"]), ("Ali", "ALI"))
        self.assertIn("/signup?agent=ALI", data["link"])
        self.assertEqual(len(data["regions"]), 14)
        self.assertEqual(data["balance"]["available"], 0)

    def test_access_rules(self):
        self.assertEqual(APIClient().get("/api/agents/app/me/").status_code, 401)
        stranger = APIClient()
        stranger.credentials(HTTP_X_TELEGRAM_INIT_DATA=init_data(999))
        self.assertEqual(stranger.get("/api/agents/app/me/").status_code, 401)

        pending = make_agent(name="Yangi", code=None, telegram_chat_id="103", is_active=False)
        pending.applied_at = pending.created_at
        pending.save()
        self.assertEqual(self.client_for(pending).get("/api/agents/app/me/").status_code, 403)

    def test_log_visit_and_see_it_in_journal(self):
        response = self.client_for(self.ali).post(
            "/api/agents/app/visits/",
            {"region": "Toshkent shahri", "name": "Rayhon", "address": "Chilonzor 9", "outcome": "interested", "comment": "Dushanba"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["reserved"]["mine"])

        rows = self.client_for(self.jasur).get("/api/agents/app/places/", {"region": "Toshkent shahri", "q": "rayh"}).json()
        place = rows["places"][0]
        self.assertEqual(place["name"], "Rayhon")
        self.assertEqual(place["reserved"]["agent"], "Ali")
        self.assertFalse(place["reserved"]["mine"])
        self.assertEqual(place["visits"][0]["comment"], "Dushanba")

        mine = self.client_for(self.jasur).get("/api/agents/app/places/", {"mine": "1"}).json()
        self.assertEqual(mine["total"], 0)

    def test_visit_to_existing_place_and_validation(self):
        place = Place.objects.create(region="Samarqand", name="Registon", address="Markaz")
        response = self.client_for(self.ali).post(
            "/api/agents/app/visits/", {"place_id": place.pk, "outcome": "refused"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Place.objects.count(), 1)

        bad = self.client_for(self.ali).post(
            "/api/agents/app/visits/", {"region": "Mars", "name": "Райхон", "address": "", "outcome": "x"}, format="json"
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(set(bad.json()), {"region", "name", "address", "outcome"})

    def test_inactive_agent_sees_only_own_things(self):
        self.ali.is_active = False
        self.ali.save()
        client = self.client_for(self.ali)

        self.assertEqual(client.get("/api/agents/app/me/").status_code, 200)
        self.assertEqual(client.get("/api/agents/app/places/", {"mine": "1"}).status_code, 200)
        self.assertEqual(client.get("/api/agents/app/places/").status_code, 403)
        self.assertEqual(client.get("/api/agents/app/partners/").status_code, 403)
        self.assertEqual(
            client.post("/api/agents/app/visits/", {"place_id": 1, "outcome": "refused"}, format="json").status_code, 403
        )

    def test_partners_and_restaurants(self):
        make_restaurant(slug="milliy", name="Milliy", region="Samarqand")
        mine = make_restaurant(slug="rayhon", name="Rayhon", subscription_status=Subscription.Status.TRIALING)
        mine.agent = self.ali
        mine.save()

        partners = self.client_for(self.ali).get("/api/agents/app/partners/", {"region": "Samarqand"}).json()
        self.assertEqual([row["name"] for row in partners], ["Milliy"])

        restaurants = self.client_for(self.ali).get("/api/agents/app/restaurants/").json()
        self.assertEqual(restaurants[0]["name"], "Rayhon")
        self.assertEqual(restaurants[0]["status"], "trialing")

    def test_card_and_withdraw(self):
        client = self.client_for(self.ali)
        self.assertEqual(client.post("/api/agents/app/withdraw/").status_code, 400)  # karta yo'q

        card = client.post("/api/agents/app/card/", {"number": "8600 1234 5678 9012", "holder": "ali valiyev"}, format="json")
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["card_holder"], "ALI VALIYEV")

        restaurant = make_restaurant(slug="rayhon", name="Rayhon")
        AgentEarning.objects.create(agent=self.ali, restaurant=restaurant, restaurant_name="Rayhon", kind="first", amount=150_000)
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/api/agents/app/withdraw/")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(AgentWithdrawal.objects.get().amount, 150_000)

        earnings = client.get("/api/agents/app/earnings/").json()
        self.assertEqual(len(earnings["months"]), 6)
        self.assertEqual(earnings["months"][-1]["amount"], 150_000)
        self.assertEqual(earnings["withdrawals"][0]["status"], "pending")
