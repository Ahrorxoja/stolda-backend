"""Restoran QR kodi: rang, o'rtada logotip va skanerlanishini o'zimiz tekshirish.

Qoidalar (aks holda telefon QR'ni o'qimay qolishi mumkin):

* Fon doim oq, nuqtalar esa to'q rangda. Teskari (och nuqta, to'q fon) QR'ni
  ko'p Android kameralari o'qimaydi, shuning uchun bunday tanlov yo'q.
* Rang oq fonga nisbatan kamida `MIN_CONTRAST` kontrastda bo'ladi — ekranda
  emas, restoranning xira yorug'ida qog'ozdan o'qiladi.
* Logotip qo'yilsa, QR eng yuqori tuzatish darajasida (H, ~30% qismi yopilsa
  ham o'qiladi) yasaladi, logotip esa tomonning `LOGO_SHARE` qismidan
  oshmaydi. Burchakdagi uchta katta kvadratga tegmaydi.
* Har bir tayyor rasm `zxing-cpp` bilan qayta o'qib ko'riladi. Logotip bilan
  o'qilmasa, logotipsiz variant beriladi — ishlamaydigan QR hech qachon
  chiqmaydi.
"""

import logging
import re

import qrcode
import zxingcpp
from PIL import Image, ImageDraw, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_COLOR = "#231C17"
BACKGROUND = "#FFFFFF"
#: WCAG kontrast nisbati (1–21). Qora — 17, oltinrang `#9E7018` — 4.4.
MIN_CONTRAST = 4.0
#: Logotip (oq hoshiyasi bilan) QR tomonining qancha qismini egallaydi.
LOGO_SHARE = 0.28

HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


class QrColorError(ValueError):
    """Rang noto'g'ri yoki QR skanerlanishi uchun juda och."""


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(color: str, background: str = BACKGROUND) -> float:
    lighter, darker = sorted((_luminance(color), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def normalize_color(value: str) -> str:
    """`9e7018`, `#9E7018` → `#9e7018`; skanerlanmaydigan och rangni rad etadi."""
    match = HEX.match((value or "").strip())
    if not match:
        raise QrColorError("Rang #RRGGBB ko'rinishida bo'lishi kerak, masalan #231C17.")
    color = f"#{match.group(1).lower()}"
    if contrast_ratio(color) < MIN_CONTRAST:
        raise QrColorError(
            "Bu rang juda och — QR kod skanerlanmay qolishi mumkin. To'qroq rang tanlang."
        )
    return color


def _logo_badge(logo_file, side: int) -> Image.Image | None:
    """Logotip — oq, yumaloq burchakli hoshiya ichida, `side` x `side` piksel."""
    try:
        logo_file.open("rb")
        with Image.open(logo_file) as source:
            logo = ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, ValueError):
        logger.warning("QR uchun logotip ochilmadi: %s", getattr(logo_file, "name", ""))
        return None
    finally:
        try:
            logo_file.close()
        except (OSError, ValueError):
            pass

    badge = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(badge).rounded_rectangle(
        (0, 0, side - 1, side - 1), radius=round(side * 0.22), fill=BACKGROUND
    )

    inner = round(side * 0.8)
    logo = ImageOps.contain(logo, (inner, inner), Image.Resampling.LANCZOS)
    mask = Image.new("L", logo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, logo.width - 1, logo.height - 1), radius=round(min(logo.size) * 0.18), fill=255
    )
    alpha = Image.composite(logo.getchannel("A"), mask, mask)
    logo.putalpha(alpha)
    badge.alpha_composite(logo, ((side - logo.width) // 2, (side - logo.height) // 2))
    return badge


def render(url: str, color: str = DEFAULT_COLOR, logo_file=None, box_size: int = 12) -> Image.Image:
    """QR rasmi (RGB). `logo_file` — `ImageField` fayli yoki `None`."""
    qr = qrcode.QRCode(
        box_size=box_size,
        border=2,
        error_correction=(
            qrcode.constants.ERROR_CORRECT_H if logo_file else qrcode.constants.ERROR_CORRECT_M
        ),
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color=color, back_color=BACKGROUND).convert("RGBA")

    if logo_file:
        code_side = qr.modules_count * box_size
        side = round(code_side * LOGO_SHARE)
        badge = _logo_badge(logo_file, side)
        if badge is not None:
            offset = (image.width - side) // 2
            image.alpha_composite(badge, (offset, offset))

    return image.convert("RGB")


def decodes_to(image: Image.Image, url: str) -> bool:
    """Rasmni haqiqiy skaner kabi o'qib ko'radi."""
    try:
        results = zxingcpp.read_barcodes(image)
    except Exception:  # noqa: BLE001 — dekoder xatosi "o'qilmadi" degani
        logger.exception("QR dekoderida xato")
        return False
    return any(result.text == url for result in results)


def restaurant_qr(
    restaurant, color: str | None = None, logo: bool | None = None, box_size: int = 12
) -> tuple[Image.Image, bool]:
    """Restoran sozlamalari (yoki oldindan ko'rish uchun berilgan qiymatlar) bilan QR.

    Qaytaradi: `(rasm, logotip_qo'yildimi)`. Logotip QR'ni o'qib bo'lmaydigan
    qilsa yoki logotip yuklanmagan bo'lsa — logotipsiz rasm.
    """
    url = restaurant.qr_url
    color = color or restaurant.qr_color or DEFAULT_COLOR
    want_logo = restaurant.qr_logo if logo is None else logo

    if want_logo and restaurant.logo:
        image = render(url, color, restaurant.logo, box_size)
        if decodes_to(image, url):
            return image, True
        logger.warning("Logotipli QR o'qilmadi, logotipsiz beriladi: %s", restaurant.slug)

    return render(url, color, None, box_size), False
