"""Public menyu keshi.

`cache_page` o'rniga versiyalangan kalit ishlatiladi: TTL 60 soniya, lekin egasi
menyuni tahrirlaganda versiya oshadi va o'zgarish darhol ko'rinadi.
"""

from django.core.cache import cache

MENU_TTL_SECONDS = 60
VERSION_TTL_SECONDS = 60 * 60 * 24 * 30


def _version_key(slug: str) -> str:
    return f"menu-version:{slug}"


def menu_version(slug: str) -> int:
    version = cache.get(_version_key(slug))
    if version is None:
        version = 1
        cache.set(_version_key(slug), version, VERSION_TTL_SECONDS)
    return version


def bump_menu_version(slug: str) -> None:
    """Menyu o'zgargach chaqiriladi — eski kesh kaliti ishlatilmay qoladi."""
    try:
        cache.incr(_version_key(slug))
    except ValueError:
        cache.set(_version_key(slug), 1, VERSION_TTL_SECONDS)


def menu_cache_key(slug: str, host: str, bucket: str = "") -> str:
    """Kesh kaliti.

    Rasm havolalari absolyut — shuning uchun xost ham kalitga kiradi.
    `bucket` — vaqtga bog'liq kategoriyalar uchun daqiqa belgisi.
    """
    return f"menu:{slug}:{host}:{bucket}:{menu_version(slug)}"


def schedule_flag_key(slug: str) -> str:
    """Restoranda vaqtga bog'liq kategoriya bormi — versiyaga bog'langan bayroq."""
    return f"menu-schedule:{slug}:{menu_version(slug)}"


def inactive_menu_cache_key(slug: str) -> str:
    """Obuna `suspended` bo'lganda qaytariladigan qisqa javob uchun.

    Xuddi shu versiyaga bog'langan — to'lovdan keyin `bump_menu_version`
    chaqirilsa, bu ham darhol eskiradi.
    """
    return f"menu-inactive:{slug}:{menu_version(slug)}"
