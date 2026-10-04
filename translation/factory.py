from ai.client import configured_keys, configured_models

from .base import Translator
from .fake import FakeTranslator
from .gemini import GeminiTranslator


def get_translator() -> Translator:
    """Sozlamalarga qarab tarjima provayderini qaytaradi.

    Kalit bo'lmasa `FakeTranslator` — shunda dev muhitida va testlarda tashqi
    so'rov yuborilmaydi va tarjima oqimi baribir ishlaydi. Bir nechta kalit
    (`GEMINI_API_KEYS`) va zaxira model (`GEMINI_FALLBACK_MODEL`) — `ai.client`.
    """
    keys = configured_keys()
    if not keys:
        return FakeTranslator()
    return GeminiTranslator(keys, configured_models())
