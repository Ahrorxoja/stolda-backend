from django.conf import settings

from .base import Translator
from .fake import FakeTranslator
from .gemini import GeminiTranslator


def get_translator() -> Translator:
    """Sozlamalarga qarab tarjima provayderini qaytaradi.

    `GEMINI_API_KEY` bo'lmasa `FakeTranslator` — shunda dev muhitida va
    testlarda tashqi so'rov yuborilmaydi va tarjima oqimi baribir ishlaydi.
    """
    key = getattr(settings, "GEMINI_API_KEY", "")
    if not key:
        return FakeTranslator()
    return GeminiTranslator(key, model=getattr(settings, "GEMINI_MODEL", "gemini-3.1-flash-lite"))
