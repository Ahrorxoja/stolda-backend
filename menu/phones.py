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
