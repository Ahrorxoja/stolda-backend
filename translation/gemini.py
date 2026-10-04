"""Gemini API orqali tarjima."""

import json
import logging
from typing import Sequence

import requests

from ai.client import DEFAULT_MODEL, AiError, GeminiClient

from .base import TranslationError, Translator

logger = logging.getLogger(__name__)

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
    """Bir so'rovda barcha tillarga tarjima qiladi.

    So'rovni `ai.GeminiClient` yuboradi — bir nechta kalit va zaxira model
    shu yerda ham ishlaydi.
    """

    def __init__(
        self,
        api_key: str | Sequence[str],
        model: str | Sequence[str] = DEFAULT_MODEL,
        timeout: int = 30,
        session: requests.Session | None = None,
    ):
        self.client = GeminiClient(api_key, model, timeout=timeout, session=session)

    def translate(
        self, texts: dict[str, str], source: str, targets: Sequence[str]
    ) -> dict[str, dict[str, str]]:
        targets = [lang for lang in targets if lang != source]
        if not texts or not targets:
            return {}

        try:
            parsed = self.client.generate_json(
                SYSTEM_PROMPT, self._user_prompt(texts, source, targets), temperature=0.2
            )
        except AiError as error:
            raise TranslationError(str(error)) from error
        if not isinstance(parsed, dict):
            raise TranslationError("Gemini JSON qaytarmadi.")

        return self._parse(parsed, texts, targets)

    def _user_prompt(self, texts: dict[str, str], source: str, targets: Sequence[str]) -> str:
        return (
            f"Manba til: {source}\n"
            f"Maqsadli tillar: {', '.join(targets)}\n\n"
            "Quyidagi JSON dagi har bir qiymatni har bir maqsadli tilga tarjima qil.\n"
            "Javob shakli: {\"<til>\": {\"<kalit>\": \"<tarjima>\"}} — kalitlar aynan o'zgarmasin.\n\n"
            f"{json.dumps(texts, ensure_ascii=False, indent=1)}"
        )

    def _parse(
        self, parsed: dict, texts: dict[str, str], targets: Sequence[str]
    ) -> dict[str, dict[str, str]]:
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
