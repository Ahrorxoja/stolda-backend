"""Menyu matnlarini avtomatik tarjima qilish.

`get_translator()` sozlamalarga qarab provayderni tanlaydi. Kalit berilmasa
`FakeTranslator` qaytadi — shunda dev muhitida va testlarda tashqi so'rov
bo'lmaydi.
"""

from .base import TranslationError, Translator
from .factory import get_translator
from .fake import FakeTranslator
from .gemini import GeminiTranslator

__all__ = [
    "FakeTranslator",
    "GeminiTranslator",
    "TranslationError",
    "Translator",
    "get_translator",
]
