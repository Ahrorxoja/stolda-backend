"""Restoran `slug`i — manzilda ishlatiladi (`stolda.uz/{slug}`)."""

from django.utils.text import slugify

from .models import Restaurant

#: Next.js'dagi tub yo'llar bilan to'qnashmasligi uchun taqiqlangan manzillar.
RESERVED_SLUGS = {
    "admin", "api", "app", "dev", "media", "signup", "static", "www",
    # Landing'ning til sahifalari (`/ru`, `/en`) va xizmat yo'llari —
    # restoran shu manzilni olsa, uning menyusi ochilmay qolardi.
    "uz", "ru", "en", "join", "sitemap", "robots", "brand", "icon", "django-admin",
}


def normalize_slug(value: str) -> str:
    return slugify(value or "", allow_unicode=False)[:60]


def unique_slug(name: str) -> str:
    """Restoran nomidan bo'sh manzil yasaydi: `Zamin` → `zamin`, `zamin-2`, …"""
    base = normalize_slug(name) or "restoran"
    if base in RESERVED_SLUGS:
        base = f"{base}-restoran"
    slug, counter = base, 1
    while Restaurant.objects.filter(slug=slug).exists():
        counter += 1
        slug = f"{base}-{counter}"
    return slug[:60]
