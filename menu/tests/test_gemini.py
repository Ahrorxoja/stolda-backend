"""GeminiTranslator: so'rov shakli va xatolarni qayta ishlash.

Haqiqiy API'ga chiqmaymiz — `requests.Session` o'rniga soxta sessiya beramiz.
"""

import json
from unittest.mock import Mock

import requests
from django.test import SimpleTestCase

from translation import GeminiTranslator, TranslationError

KEY = "sinov-kaliti-12345"


def response(status: int, payload: dict | None = None, text: str = "") -> Mock:
    fake = Mock(spec=requests.Response)
    fake.status_code = status
    fake.ok = 200 <= status < 300
    fake.json.return_value = payload if payload is not None else {}
    fake.text = text
    return fake


def gemini_reply(data: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}}]}


class GeminiRequestTests(SimpleTestCase):
    def translator(self, fake_response):
        session = Mock(spec=requests.Session)
        session.post.return_value = fake_response
        return GeminiTranslator(KEY, session=session), session

    def test_sends_the_key_in_a_header_not_the_url(self):
        """Kalit URL'da bo'lsa, xato matnlari va loglarga tushib qolardi."""
        translator, session = self.translator(
            response(200, gemini_reply({"ru": {"name": "Долма"}}))
        )

        translator.translate({"name": "Do'lma"}, source="uz", targets=["ru"])

        url, kwargs = session.post.call_args[0][0], session.post.call_args[1]
        self.assertNotIn(KEY, url)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], KEY)

    def test_returns_only_the_requested_keys(self):
        translator, _ = self.translator(
            response(200, gemini_reply({"ru": {"name": "Долма", "qoshimcha": "..."}}))
        )

        result = translator.translate({"name": "Do'lma"}, source="uz", targets=["ru"])

        self.assertEqual(result, {"ru": {"name": "Долма"}})

    def test_skips_the_source_language(self):
        translator, session = self.translator(response(200, gemini_reply({})))

        result = translator.translate({"name": "Do'lma"}, source="uz", targets=["uz"])

        self.assertEqual(result, {})
        session.post.assert_not_called()

    def test_missing_language_is_left_out(self):
        translator, _ = self.translator(
            response(200, gemini_reply({"ru": {"name": "Долма"}}))
        )

        result = translator.translate(
            {"name": "Do'lma"}, source="uz", targets=["ru", "en"]
        )

        self.assertEqual(list(result), ["ru"])


class GeminiErrorTests(SimpleTestCase):
    def translator(self, fake_response):
        session = Mock(spec=requests.Session)
        session.post.return_value = fake_response
        return GeminiTranslator(KEY, session=session)

    def assert_no_key(self, message: str):
        self.assertNotIn(KEY, message)

    def test_rate_limit_message_is_readable_and_safe(self):
        translator = self.translator(response(429, {"error": {"message": "quota"}}))

        with self.assertRaises(TranslationError) as caught:
            translator.translate({"name": "Do'lma"}, source="uz", targets=["ru"])

        self.assertIn("429", str(caught.exception))
        self.assert_no_key(str(caught.exception))

    def test_bad_key_message_is_readable_and_safe(self):
        translator = self.translator(response(403, {"error": {"message": "bad key"}}))

        with self.assertRaises(TranslationError) as caught:
            translator.translate({"name": "Do'lma"}, source="uz", targets=["ru"])

        self.assert_no_key(str(caught.exception))

    def test_network_failure_is_wrapped(self):
        session = Mock(spec=requests.Session)
        session.post.side_effect = requests.ConnectionError(f"failed for key={KEY}")
        translator = GeminiTranslator(KEY, session=session)

        with self.assertRaises(TranslationError) as caught:
            translator.translate({"name": "Do'lma"}, source="uz", targets=["ru"])

        self.assert_no_key(str(caught.exception))

    def test_non_json_answer_is_wrapped(self):
        translator = self.translator(
            response(200, {"candidates": [{"content": {"parts": [{"text": "salom"}]}}]})
        )

        with self.assertRaises(TranslationError):
            translator.translate({"name": "Do'lma"}, source="uz", targets=["ru"])

    def test_empty_key_is_refused(self):
        with self.assertRaises(ValueError):
            GeminiTranslator("")
