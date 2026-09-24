"""Restoranning haftalik ish vaqti.

Erkin matn o'rniga tuzilma: har kun uchun ochilish/yopilish vaqti yoki
"dam olish kuni". Vaqtlar raqam bo'lgani uchun tarjima qilinmaydi — mijoz
menyusida kun nomlari brauzer tomonidan tanlangan tilga o'giriladi.
"""

import re

from django.core.exceptions import ValidationError

#: Dushanbadan yakshanbagacha — tartib muhim, menyuda shu tartibda ko'rsatiladi.
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def day_hours(open_at: str = "10:00", close_at: str = "22:00", closed: bool = False) -> dict:
    return {"open": open_at, "close": close_at, "closed": closed}


def default_working_hours() -> dict:
    """Yangi restoran uchun: har kuni 10:00–22:00."""
    return {day: day_hours() for day in DAYS}


def validate_working_hours(value) -> None:
    if not isinstance(value, dict):
        raise ValidationError("Ish vaqti obyekt bo'lishi kerak.")
    unknown = set(value) - set(DAYS)
    if unknown:
        raise ValidationError(f"Noma'lum kun: {', '.join(sorted(unknown))}.")
    for day, entry in value.items():
        if not isinstance(entry, dict):
            raise ValidationError(f"{day}: obyekt bo'lishi kerak.")
        if not isinstance(entry.get("closed", False), bool):
            raise ValidationError(f"{day}: `closed` mantiqiy qiymat bo'lishi kerak.")
        for key in ("open", "close"):
            time = entry.get(key)
            if not isinstance(time, str) or not _TIME.match(time):
                raise ValidationError(f"{day}.{key}: vaqt HH:MM ko'rinishida bo'lsin.")


def parse_hours_text(text: str) -> dict | None:
    """Eski erkin matndan (`"Har kuni 10:00 – 23:00"`) vaqtni ajratadi.

    Ikkita vaqt topilsa — o'shani hamma kunga qo'yadi, aks holda `None`.
    Faqat ma'lumot ko'chirishda ishlatiladi.
    """
    found = re.findall(r"([01]?\d|2[0-3]):([0-5]\d)", text or "")
    if len(found) < 2:
        return None
    start, end = found[0], found[1]
    open_at = f"{int(start[0]):02d}:{start[1]}"
    close_at = f"{int(end[0]):02d}:{end[1]}"
    return {day: day_hours(open_at, close_at) for day in DAYS}
