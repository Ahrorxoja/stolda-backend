"""AI yordamchi: endpointlar, `kcal_source`, Gemini mijozi (kalitlar, zaxira model)."""

import json
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from ai import AiError, DishFacts, GeminiAssistant, GeminiClient, configured_keys, configured_models
from ai import client as ai_client
from menu.models import Dish, KcalSource

from .factories import make_category, make_dish, make_restaurant
from .test_admin_api import AdminApiTestCase

NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


class AiViewTestCase(AdminApiTestCase):
    def post(self, route, **payload):
        body = {"restaurant": self.restaurant.pk, **payload}
        return self.client.post(
            reverse(route), body, content_type="application/json", **self.auth()
        )


class KcalAiViewTests(AiViewTestCase):
    def test_returns_an_estimate_without_saving(self):
        response = self.post(
            "ai-kcal", name="Palov", weight=300, ingredients=["Guruch"], category=self.category.pk
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"kcal": 450, "low": 382, "high": 518})
        self.assertFalse(Dish.objects.exists())

    def test_weight_is_required(self):
        self.assertEqual(self.post("ai-kcal", name="Palov").status_code, 400)

    def test_another_owners_restaurant_is_refused(self):
        other = make_restaurant(slug="boshqa", name="Boshqa")

        response = self.post("ai-kcal", restaurant=other.pk, name="Palov", weight=300)

        self.assertEqual(response.status_code, 403)

    def test_provider_failure_is_a_502(self):
        failing = Mock()
        failing.estimate_kcal.side_effect = AiError("limit")
        with patch("menu.ai_views.get_assistant", return_value=failing):
            response = self.post("ai-kcal", name="Palov", weight=300)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "limit")

    def test_requires_a_token(self):
        response = self.client.post(
            reverse("ai-kcal"),
            {"restaurant": self.restaurant.pk, "name": "Palov", "weight": 300},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)


class IngredientsAiViewTests(AiViewTestCase):
    def test_tidies_the_list(self):
        response = self.post("ai-ingredients", items=["  guruch ", "Guruch", "mol goʻshti"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": ["Guruch", "Mol go'shti"]})

    def test_empty_list_is_refused(self):
        self.assertEqual(self.post("ai-ingredients", items=[]).status_code, 400)


class DescriptionAiViewTests(AiViewTestCase):
    def test_writes_in_the_primary_language(self):
        response = self.post("ai-description", name="Palov", ingredients=["Guruch", "Sabzi"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["variants"][0], "[uz] Guruch, Sabzi.")


class KcalSourceTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.dish = make_dish(self.category, kcal=500, kcal_source=KcalSource.AI)
        self.url = reverse("dish-detail", args=[self.dish.pk])

    def patch(self, **data):
        return self.client.patch(self.url, data, content_type="application/json", **self.auth())

    def test_ai_source_is_saved(self):
        self.patch(kcal=620, kcal_source="ai")

        self.dish.refresh_from_db()
        self.assertEqual((self.dish.kcal, self.dish.kcal_source), (620, "ai"))

    def test_changing_kcal_without_source_makes_it_manual(self):
        """Telegram yoki eski klient faqat `kcal` yuborsa — endi odam kiritgan."""
        self.patch(kcal=480)

        self.dish.refresh_from_db()
        self.assertEqual(self.dish.kcal_source, "manual")

    def test_resending_the_same_kcal_keeps_the_ai_mark(self):
        self.patch(kcal=500, price=50000)

        self.dish.refresh_from_db()
        self.assertEqual(self.dish.kcal_source, "ai")


@NO_CACHE
class PublicKcalEstimatedTests(TestCase):
    def test_only_ai_kcal_is_marked_estimated(self):
        restaurant = make_restaurant()
        category = make_category(restaurant)
        make_dish(category, kcal=500, kcal_source=KcalSource.AI, position=0)
        make_dish(category, kcal=300, position=1)
        make_dish(category, kcal=None, kcal_source=KcalSource.AI, position=2)

        dishes = self.client.get(reverse("public-menu", args=[restaurant.slug])).json()["dishes"]

        self.assertEqual([dish["kcal_estimated"] for dish in dishes], [True, False, False])


def reply(data: object = None, status: int = 200) -> Mock:
    fake = Mock(spec=requests.Response)
    fake.status_code = status
    fake.ok = 200 <= status < 300
    fake.json.return_value = (
        {"candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}}]}
        if fake.ok
        else {"error": {"message": "x"}}
    )
    return fake


def used_keys(session: Mock) -> list[str]:
    return [call.kwargs["headers"]["x-goog-api-key"] for call in session.post.call_args_list]


def used_models(session: Mock) -> list[str]:
    return [call.args[0].rsplit("/", 1)[1].split(":")[0] for call in session.post.call_args_list]


class GeminiClientTests(SimpleTestCase):
    def setUp(self):
        ai_client._cooldown.clear()

    def client_with(self, replies, keys=("k1", "k2"), models=("m1",)):
        session = Mock(spec=requests.Session)
        session.post.side_effect = replies
        return GeminiClient(list(keys), list(models), session=session), session

    def test_key_is_sent_in_a_header_not_the_url(self):
        client, session = self.client_with([reply({"ok": 1})], keys=("kalit-123",))

        self.assertEqual(client.generate_json("s", "p"), {"ok": 1})
        self.assertNotIn("kalit-123", session.post.call_args.args[0])

    def test_rate_limited_key_falls_through_to_the_next(self):
        client, session = self.client_with([reply(status=429), reply({"ok": 1})])

        self.assertEqual(client.generate_json("s", "p"), {"ok": 1})
        self.assertEqual(used_keys(session), ["k1", "k2"])

    def test_rate_limited_key_rests_for_the_next_request(self):
        client, session = self.client_with([reply(status=429), reply({"a": 1}), reply({"b": 2})])

        client.generate_json("s", "p")
        client.generate_json("s", "p")

        self.assertEqual(used_keys(session), ["k1", "k2", "k2"])

    def test_fallback_model_is_tried_when_every_key_is_limited(self):
        client, session = self.client_with(
            [reply(status=429), reply(status=429), reply({"ok": 1})], models=("m1", "m2")
        )

        self.assertEqual(client.generate_json("s", "p"), {"ok": 1})
        self.assertEqual(used_models(session), ["m1", "m1", "m2"])

    def test_busy_provider_is_retried_once(self):
        client, session = self.client_with([reply(status=503), reply({"ok": 1})], keys=("k1",))

        self.assertEqual(client.generate_json("s", "p"), {"ok": 1})
        self.assertEqual(session.post.call_count, 2)

    def test_everything_limited_gives_a_readable_error_without_keys(self):
        client, _ = self.client_with([reply(status=429), reply(status=429)])

        with self.assertRaises(AiError) as caught:
            client.generate_json("s", "p")

        self.assertIn("429", str(caught.exception))
        self.assertNotIn("k1", str(caught.exception))

    def test_bad_request_is_not_retried_with_other_keys(self):
        client, session = self.client_with([reply(status=400)])

        with self.assertRaises(AiError):
            client.generate_json("s", "p")
        self.assertEqual(session.post.call_count, 1)

    @override_settings(
        GEMINI_API_KEY="k1", GEMINI_API_KEYS=" k2, k1 ,,k3", GEMINI_MODEL="m1",
        GEMINI_FALLBACK_MODEL="m2",
    )
    def test_keys_and_models_come_from_settings(self):
        self.assertEqual(configured_keys(), ["k1", "k2", "k3"])
        self.assertEqual(configured_models(), ["m1", "m2"])


class GeminiAssistantTests(SimpleTestCase):
    facts = DishFacts(name="Palov", weight=300, ingredients=["Guruch", "Sabzi"])

    def setUp(self):
        ai_client._cooldown.clear()

    def assistant(self, data):
        session = Mock(spec=requests.Session)
        session.post.return_value = reply(data)
        return GeminiAssistant(GeminiClient("k", session=session)), session

    def prompt(self, session: Mock) -> str:
        return session.post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]

    def test_kcal_prompt_has_weight_and_ingredients(self):
        assistant, session = self.assistant({"kcal": 560, "low": 650, "high": 480})

        result = assistant.estimate_kcal(self.facts)

        self.assertIn("300 g", self.prompt(session))
        self.assertIn("Guruch, Sabzi", self.prompt(session))
        self.assertEqual((result.kcal, result.low, result.high), (560, 480, 650))

    def test_absurd_kcal_is_rejected(self):
        """300 g taomda 5000 kkal bo'lishi mumkin emas (sof yog' ham ~2700)."""
        assistant, _ = self.assistant({"kcal": 5000})

        with self.assertRaises(AiError):
            assistant.estimate_kcal(self.facts)

    def test_fixed_ingredients_are_tidied_again(self):
        """Model ham takror yoki boshqa apostrof qaytarishi mumkin."""
        assistant, _ = self.assistant({"items": ["guruch", "Guruch", "Goʻsht", ""]})

        self.assertEqual(assistant.fix_ingredients(["gurch", "gosht"], "uz"), ["Guruch", "Go'sht"])

    def test_empty_fix_for_a_full_list_is_an_error(self):
        assistant, _ = self.assistant({"items": []})

        with self.assertRaises(AiError):
            assistant.fix_ingredients(["Guruch"], "uz")

    def test_long_and_duplicate_descriptions_are_dropped(self):
        assistant, _ = self.assistant(
            {"variants": ["«Qisqa tavsif.»", "Qisqa tavsif.", "x" * 300, 5]}
        )

        self.assertEqual(assistant.describe(self.facts), ["Qisqa tavsif."])

    def test_description_prompt_names_the_language(self):
        assistant, session = self.assistant({"variants": ["Плов с морковью."]})

        assistant.describe(DishFacts(name="Плов", lang="ru"))

        system = session.post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("rus tili", system)
