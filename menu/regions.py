"""O'zbekiston hududlari — restoran, agent va agentlar jurnali uchun yagona ro'yxat.

Frontend'da xuddi shu ro'yxat `lib/regions.ts` da — ikkalasi mos bo'lsin.
"""

import re

#: 12 viloyat, Qoraqalpog'iston Respublikasi va Toshkent shahri.
REGIONS = [
    "Toshkent shahri",
    "Toshkent viloyati",
    "Andijon",
    "Buxoro",
    "Farg'ona",
    "Jizzax",
    "Xorazm",
    "Namangan",
    "Navoiy",
    "Qashqadaryo",
    "Qoraqalpog'iston",
    "Samarqand",
    "Sirdaryo",
    "Surxondaryo",
]
REGION_CHOICES = [(name, name) for name in REGIONS]

_APOSTROPHES = re.compile(r"[ʻʼ'`’‘\s]")


def match_region(text: str) -> str:
    """Qo'lda yozilgan nomni ro'yxatdagisiga moslaydi ("fargona" → "Farg'ona")."""
    key = _APOSTROPHES.sub("", text or "").lower()
    for region in REGIONS:
        if _APOSTROPHES.sub("", region).lower() == key:
            return region
    return ""
