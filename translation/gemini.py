"""Gemini API orqali tarjima."""

import json
import logging
from typing import Sequence

import requests

from .base import TranslationError, Translator

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_PROMPT = """Sen restoran menyusini tarjima qilasan.

Qoidalar:
- Faqat tarjimani qaytar, izoh yozma.
- **Maqsadli tilning yozuvidan foydalan.** Rus va o'zbek-kirill uchun kirill,
  ingliz/turk/nemis uchun lotin, xitoy uchun ieroglif, koreys uchun hangul,
  arab uchun arab yozuvi. Lotin yozuvini boshqa yozuvli tilga ko'chirma.
- Milliy taom nomlarini ma'nosiga ko'ra tarjima qilma — maqsadli til yozuvida
  transliteratsiya qil. Masalan "Do'lma" → ru: "Долма", en: "Dolma", ar: "دولما".
  "Lag'mon" → ru: "Лагман", en: "Lagman". Qavs ichida izoh qo'shma.
- Umumiy oshxona atamalarini (sabzavot, go'sht nomlari) o'sha tilda odatda
  qanday yozilsa shunday tarjima qil.
- Matn uzunligini taxminan saqla: menyuda joy cheklangan.
- Bo'sh matnni bo'sh qoldir.
- Javob faqat JSON bo'lsin."""


class GeminiTranslator(Translator):
    """Bir so'rovda barcha tillarga tarjima qiladi."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.1-flash-lite",
        timeout: int = 30,
        session: requests.Session | None = None,
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY berilmagan.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    def translate(
        self, texts: dict[str, str], source: str, targets: Sequence[str]
    ) -> dict[str, dict[str, str]]:
        targets = [lang for lang in targets if lang != source]
        if not texts or not targets:
            return {}

        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": self._user_prompt(texts, source, targets)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
            },
        }

        try:
            response = self.session.post(
                f"{API_ROOT}/{self.model}:generateContent",
                # Kalit sarlavhada — `requests` xatolarida URL ko'rinib qoladi.
                headers={"x-goog-api-key": self.api_key},
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise TranslationError("Gemini API'ga ulanib bo'lmadi.") from error

        if not response.ok:
            raise TranslationError(self._failure(response))

        try:
            body = response.json()
        except ValueError as error:
            raise TranslationError("Gemini javobini o'qib bo'lmadi.") from error

        return self._parse(body, texts, targets)

    def _failure(self, response: requests.Response) -> str:
        """Xato matni — kalit hech qachon tushib qolmasligi kerak."""
        reason = ""
        try:
            reason = response.json().get("error", {}).get("message", "")
        except ValueError:
            pass

        if response.status_code == 429:
            return "Gemini limiti tugadi (429). Tarifni yoki modelni tekshiring."
        if response.status_code in (401, 403):
            return "Gemini kaliti qabul qilinmadi (403). Kalitni tekshiring."
        return f"Gemini xatosi ({response.status_code}). {reason}".strip()

    def _user_prompt(self, texts: dict[str, str], source: str, targets: Sequence[str]) -> str:
        return (
            f"Manba til: {source}\n"
            f"Maqsadli tillar: {', '.join(targets)}\n\n"
            "Quyidagi JSON dagi har bir qiymatni har bir maqsadli tilga tarjima qil.\n"
            "Javob shakli: {\"<til>\": {\"<kalit>\": \"<tarjima>\"}} — kalitlar aynan o'zgarmasin.\n\n"
            f"{json.dumps(texts, ensure_ascii=False, indent=1)}"
        )

    def _parse(
        self, body: dict, texts: dict[str, str], targets: Sequence[str]
    ) -> dict[str, dict[str, str]]:
        try:
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as error:
            raise TranslationError("Gemini javobi kutilganidek emas.") from error

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            logger.warning("Gemini JSON qaytarmadi: %s", raw[:200])
            raise TranslationError("Gemini JSON qaytarmadi.") from error

        result: dict[str, dict[str, str]] = {}
        for lang in targets:
            translated = parsed.get(lang)
            if not isinstance(translated, dict):
                logger.warning("Gemini '%s' tili uchun natija bermadi", lang)
                continue
            # Faqat so'ralgan kalitlar, va faqat matn qiymatlar.
            result[lang] = {
                key: str(translated.get(key, "") or "") for key in texts
            }
        return result
