"""Telefon raqami bilan kirish. Raqam `User.username` da saqlanadi."""

import re

DIGITS = re.compile(r"\D+")


def normalize_phone(value: str) -> str:
    """`+998 90 123 45 67`, `998901234567`, `901234567` → `+998901234567`."""
    digits = DIGITS.sub("", value or "")
    if not digits:
        return ""
    if len(digits) == 9:
        digits = f"998{digits}"
    return f"+{digits}"


#: Asosiydan tashqari shuncha raqam qo'shsa bo'ladi.
MAX_EXTRA_PHONES = 4

#: O'zbekiston raqami 12 ta raqamdan iborat (998 + 9). Pastki chegara 11 —
#: chet el raqami ham yozilishi mumkin; yuqorisi xalqaro E.164 standarti.
MIN_PHONE_DIGITS = 11
MAX_PHONE_DIGITS = 15


def is_valid_phone(value: str) -> bool:
    """Raqam to'liq yozilganmi.

    `normalize_phone` o'zi tekshirmaydi — u faqat ko'rinishni bir xil qiladi:
    `"salom"` bo'sh satrga, `"90123"` esa `"+90123"` ga aylanardi va shu
    ko'rinishda saqlanib ketardi. Shuning uchun to'liqligi alohida tekshiriladi.
    """
    digits = DIGITS.sub("", value or "")
    if len(digits) == 9:  # `normalize_phone` 998 ni o'zi qo'shadi
        digits = f"998{digits}"
    return MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS


def clean_phone_list(values, exclude: str = "") -> list[str]:
    """Ro'yxatni tartibga soladi: bo'shlar tushadi, takrorlanmaydi, bir ko'rinishda.

    `exclude` — asosiy raqam; u ro'yxatda ikkinchi marta turmasin.
    """
    seen: list[str] = []
    skip = normalize_phone(exclude) if exclude else ""
    for value in values or []:
        if not isinstance(value, str):
            continue
        phone = normalize_phone(value)
        if not phone or phone == skip or phone in seen:
            continue
        seen.append(phone)
    return seen


def validate_phone_list(value) -> None:
    from django.core.exceptions import ValidationError

    if not isinstance(value, list):
        raise ValidationError("Qo'shimcha raqamlar ro'yxat bo'lishi kerak.")
    if len(value) > MAX_EXTRA_PHONES:
        raise ValidationError(
            f"Ko'pi bilan {MAX_EXTRA_PHONES} ta qo'shimcha raqam qo'shsa bo'ladi."
        )
    for item in value:
        if not isinstance(item, str) or not is_valid_phone(item):
            raise ValidationError(f"Telefon raqami noto'g'ri: {item!r}")
