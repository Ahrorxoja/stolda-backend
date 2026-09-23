from typing import Protocol, Sequence


class TranslationError(RuntimeError):
    """Tarjima provayderi javob bermadi yoki javobni o'qib bo'lmadi."""


class Translator(Protocol):
    """Tarjima provayderi interfeysi.

    `texts` — kalit → matn (masalan `{"name": "Do'lma", "description": "..."}`).
    Natija — har bir til uchun xuddi shu kalitlar: `{"ru": {"name": "Долма"}}`.
    Kalitlar to'plami o'zgarmaydi, shuning uchun chaqiruvchi javobni
    bevosita maydonlarga yoyishi mumkin.
    """

    def translate(
        self, texts: dict[str, str], source: str, targets: Sequence[str]
    ) -> dict[str, dict[str, str]]: ...
