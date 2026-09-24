"""Menyu matnlarini asosiy tildan boshqa tillarga ko'chirish.

Qoida (CLAUDE.md): asosiy tildagi matn o'zgarganda `source="ai"` bo'lgan tillar
qayta tarjima qilinadi, `manual` larga tegilmaydi.

O'zgarganini bilish uchun asosiy tildagi matnlarning xeshi saqlanadi
(`translation_meta[lang]["source_hash"]`). Xesh mos bo'lsa qayta tarjima
qilinmaydi — shuning uchun saqlash qayta-qayta tarjimaga olib kelmaydi.
"""

import hashlib
import json
from dataclasses import dataclass

from .translations import SOURCE_AI, SOURCE_MANUAL


@dataclass(frozen=True)
class Field:
    name: str
    is_list: bool = False


#: Har bir modelning tarjima qilinadigan maydonlari.
TRANSLATABLE: dict[str, tuple[Field, ...]] = {
    "Dish": (Field("name"), Field("description"), Field("ingredients", is_list=True)),
    "Category": (Field("name"), Field("subtitle")),
    "Restaurant": (Field("cuisine"), Field("address")),
}

LIST_SEPARATOR = "\n"


def fields_for(obj) -> tuple[Field, ...]:
    return TRANSLATABLE.get(type(obj).__name__, ())


def restaurant_of(obj):
    """Obyekt qaysi restoranga tegishli."""
    name = type(obj).__name__
    if name == "Restaurant":
        return obj
    if name == "Category":
        return obj.restaurant
    if name == "Dish":
        return obj.category.restaurant
    return None


def read(obj, field: Field, lang: str) -> str:
    """Maydonning bitta tildagi qiymatini matn ko'rinishida oladi."""
    value = (getattr(obj, field.name) or {}).get(lang)
    if field.is_list:
        return LIST_SEPARATOR.join(value or [])
    return value or ""


def write(obj, field: Field, lang: str, text: str) -> None:
    current = dict(getattr(obj, field.name) or {})
    if field.is_list:
        current[lang] = [line.strip() for line in text.split(LIST_SEPARATOR) if line.strip()]
    else:
        current[lang] = text
    setattr(obj, field.name, current)


def source_texts(obj, primary: str) -> dict[str, str]:
    """Asosiy tildagi matnlar: `{"name": "...", "description": "..."}`."""
    return {field.name: read(obj, field, primary) for field in fields_for(obj)}


def source_hash(obj, primary: str) -> str:
    payload = json.dumps(source_texts(obj, primary), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def pending_languages(obj, restaurant=None) -> list[str]:
    """Qaysi tillar qayta tarjima qilinishi kerak.

    `manual` tillar chetlab o'tiladi; xeshi mos tillar ham — ularda matn
    o'zgarmagan.
    """
    restaurant = restaurant or restaurant_of(obj)
    if restaurant is None or not fields_for(obj):
        return []

    current = source_hash(obj, restaurant.primary_language)
    meta = getattr(obj, "translation_meta", None) or {}

    pending = []
    for lang in restaurant.secondary_languages:
        entry = meta.get(lang) or {}
        if entry.get("source") == SOURCE_MANUAL:
            continue
        if entry.get("source_hash") == current:
            continue
        pending.append(lang)
    return pending


def has_stale_manual(obj, restaurant=None) -> list[str]:
    """Qo'lda yozilgan, lekin asosiy matn keyin o'zgargan tillar.

    Bularga tegilmaydi — admin'da "asosiy matn o'zgardi" ogohlantirishi uchun.
    """
    restaurant = restaurant or restaurant_of(obj)
    if restaurant is None:
        return []

    current = source_hash(obj, restaurant.primary_language)
    meta = getattr(obj, "translation_meta", None) or {}
    return [
        lang
        for lang, entry in meta.items()
        if entry.get("source") == SOURCE_MANUAL
        and entry.get("source_hash")
        and entry.get("source_hash") != current
    ]


def apply_translations(obj, translated: dict[str, dict[str, str]], primary: str) -> None:
    """Tarjima natijasini maydonlarga yozadi va qaydni yangilaydi."""
    digest = source_hash(obj, primary)
    meta = dict(getattr(obj, "translation_meta", None) or {})

    for lang, values in translated.items():
        for field in fields_for(obj):
            if field.name in values:
                write(obj, field, lang, values[field.name])
        meta[lang] = {"source": SOURCE_AI, "source_hash": digest}

    obj.translation_meta = meta


def mark_manual(obj, lang: str, primary: str) -> None:
    """Tilni qo'lda yozilgan deb belgilaydi — endi AI unga tegmaydi."""
    meta = dict(getattr(obj, "translation_meta", None) or {})
    meta[lang] = {"source": SOURCE_MANUAL, "source_hash": source_hash(obj, primary)}
    obj.translation_meta = meta
