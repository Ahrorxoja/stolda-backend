"""Tarjima maydonlari uchun yordamchilar.

Har bir tarjima maydoni — `JSONField`, ichida `{"uz": "...", "ru": "..."}`.
Restoran qaysi tillarni ko'rsatishini o'zi tanlaydi (`Restaurant.languages`),
bittasi asosiy (`Restaurant.primary_language`). Bo'sh yoki yo'q til uchun
asosiy tilga qaytiladi.
"""

from django.core.exceptions import ValidationError

#: Qo'llab-quvvatlanadigan tillar va ularning nomlari (admin ro'yxati uchun).
LANGUAGE_NAMES: dict[str, str] = {
    "uz": "O'zbekcha",
    "uz-Cyrl": "Ўзбекча",
    "ru": "Русский",
    "en": "English",
    "tr": "Türkçe",
    "zh": "中文",
    "ko": "한국어",
    "ar": "العربية",
    "de": "Deutsch",
}

LANGUAGES: tuple[str, ...] = tuple(LANGUAGE_NAMES)
DEFAULT_LANGUAGE = "uz"

#: Tarjima qaydi: matn AI tarjimasimi yoki qo'lda yozilganmi.
SOURCE_AI = "ai"
SOURCE_MANUAL = "manual"


def default_languages() -> list[str]:
    return [DEFAULT_LANGUAGE]


def empty_translation() -> dict[str, str]:
    return {DEFAULT_LANGUAGE: ""}


def empty_translation_list() -> dict[str, list[str]]:
    return {DEFAULT_LANGUAGE: []}


def translate(value: dict | None, lang: str = DEFAULT_LANGUAGE, fallback: str = DEFAULT_LANGUAGE):
    """Tarjima lug'atidan bitta tilni oladi, bo'sh bo'lsa asosiy tilga qaytadi."""
    if not isinstance(value, dict):
        return value
    picked = value.get(lang)
    if picked:
        return picked
    return value.get(fallback) or value.get(DEFAULT_LANGUAGE) or ""


def validate_language(code: str) -> None:
    if code not in LANGUAGE_NAMES:
        raise ValidationError(f"Noma'lum til: {code}.")


def validate_languages(value) -> None:
    """`["uz", "ru"]` — kamida bitta, faqat qo'llab-quvvatlanadigan tillar."""
    if not isinstance(value, list) or not value:
        raise ValidationError("Kamida bitta til tanlanishi kerak.")
    unknown = [code for code in value if code not in LANGUAGE_NAMES]
    if unknown:
        raise ValidationError(f"Noma'lum tillar: {', '.join(sorted(unknown))}.")
    if len(set(value)) != len(value):
        raise ValidationError("Tillar takrorlanmasligi kerak.")


def _check_keys(value) -> None:
    if not isinstance(value, dict):
        raise ValidationError("Tarjima maydoni lug'at bo'lishi kerak.")
    unknown = set(value) - set(LANGUAGE_NAMES)
    if unknown:
        raise ValidationError(f"Noma'lum tillar: {', '.join(sorted(unknown))}.")


def validate_translation(value) -> None:
    """`{"uz": "matn", ...}` ko'rinishini tekshiradi."""
    _check_keys(value)
    for lang, text in value.items():
        if not isinstance(text, str):
            raise ValidationError(f"`{lang}` qiymati matn bo'lishi kerak.")


def validate_translation_list(value) -> None:
    """`{"uz": ["..."], ...}` ko'rinishini tekshiradi (tarkib ro'yxati)."""
    _check_keys(value)
    for lang, items in value.items():
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ValidationError(f"`{lang}` qiymati matnlar ro'yxati bo'lishi kerak.")


def validate_translation_meta(value) -> None:
    """`{"ru": {"source": "ai", "source_hash": "..."}}` ko'rinishini tekshiradi."""
    _check_keys(value)
    for lang, meta in value.items():
        if not isinstance(meta, dict):
            raise ValidationError(f"`{lang}` qaydi lug'at bo'lishi kerak.")
        source = meta.get("source")
        if source not in (SOURCE_AI, SOURCE_MANUAL):
            raise ValidationError(
                f"`{lang}` uchun manba `{SOURCE_AI}` yoki `{SOURCE_MANUAL}` bo'lishi kerak."
            )
