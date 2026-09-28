"""Restoran QR kodi: rang, o'rtada logotip va skanerlanishini o'zimiz tekshirish.

Qoidalar (aks holda telefon QR'ni o'qimay qolishi mumkin):

* Fon doim oq, nuqtalar esa to'q rangda. Teskari (och nuqta, to'q fon) QR'ni
  ko'p Android kameralari o'qimaydi, shuning uchun bunday tanlov yo'q.
* Rang oq fonga nisbatan kamida `MIN_CONTRAST` kontrastda bo'ladi — ekranda
  emas, restoranning xira yorug'ida qog'ozdan o'qiladi.
* O'rtaga logotip yoki vilka-pichoq belgisi qo'yilsa, QR eng yuqori tuzatish
  darajasida (H, ~30% qismi yopilsa ham o'qiladi) yasaladi, belgi esa
  tomonning `LOGO_SHARE` qismidan oshmaydi. Burchak kvadratlariga tegmaydi.
* Nuqta shakli (`STYLES`) va burchak kvadratlari (`EYES`) faqat sinab
  ko'rilgan, ishonchli turlardan tanlanadi. Burchak kvadratlarining
  tuzilishi (7-5-3 modul) o'zgarmaydi — faqat burchaklari silliqlanadi.
* Har bir tayyor rasm `zxing-cpp` bilan qayta o'qib ko'riladi. O'qilmasa
  avval logotipsiz, keyin klassik turda beriladi — ishlamaydigan QR hech
  qachon chiqmaydi.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import qrcode
import zxingcpp
from PIL import Image, ImageDraw, ImageOps
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode.image.styles.moduledrawers.pil import (
    CircleModuleDrawer,
    GappedSquareModuleDrawer,
    RoundedModuleDrawer,
    SquareModuleDrawer,
)

logger = logging.getLogger(__name__)

DEFAULT_COLOR = "#231C17"
BACKGROUND = "#FFFFFF"
#: WCAG kontrast nisbati (1–21). Qora — 17, oltinrang `#9E7018` — 4.4.
MIN_CONTRAST = 4.0
#: Logotip (oq hoshiyasi bilan) QR tomonining qancha qismini egallaydi.
LOGO_SHARE = 0.28

HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

#: Nuqta shakli: kalit → (nomi, chizuvchi).
STYLES = {
    "square": ("Klassik", SquareModuleDrawer),
    "rounded": ("Yumaloq", RoundedModuleDrawer),
    "dots": ("Nuqtali", CircleModuleDrawer),
    "gapped": ("Kichik kvadratlar", GappedSquareModuleDrawer),
}
#: Burchakdagi uchta katta kvadrat.
EYES = {
    "square": ("Kvadrat", SquareModuleDrawer),
    "rounded": ("Yumaloq", RoundedModuleDrawer),
}
STYLE_CHOICES = [(key, name) for key, (name, _) in STYLES.items()]
EYE_CHOICES = [(key, name) for key, (name, _) in EYES.items()]
DEFAULT_STYLE = "square"
DEFAULT_EYES = "square"

#: QR o'rtasi: vilka-pichoq belgisi, restoran logotipi yoki bo'sh.
CENTERS = {"icon": "Vilka-pichoq", "logo": "Logotip", "none": "Bo'sh"}
CENTER_CHOICES = list(CENTERS.items())
DEFAULT_CENTER = "icon"

#: Shriftlar va belgilar (Lucide, ISC; Rubik va Playfair Display, OFL).
ASSETS = Path(__file__).resolve().parent / "qr_assets"


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


def _badge_frame(side: int) -> Image.Image:
    """O'rtadagi belgi uchun oq, yumaloq burchakli hoshiya."""
    badge = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(badge).rounded_rectangle(
        (0, 0, side - 1, side - 1), radius=round(side * 0.22), fill=BACKGROUND
    )
    return badge


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

    badge = _badge_frame(side)
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


def icon_mask(name: str, size: int) -> Image.Image:
    """`qr_assets/<name>.png` (Lucide belgisi) — `size` pikselli alfa niqob."""
    with Image.open(ASSETS / f"{name}.png") as source:
        return source.getchannel("A").resize((size, size), Image.Resampling.LANCZOS)


def _icon_badge(color: str, side: int) -> Image.Image:
    """Vilka-pichoq — "bu menyu" ekanini birinchi qarashda bildiradi."""
    badge = _badge_frame(side)
    inner = round(side * 0.58)
    tint = Image.new("RGBA", (inner, inner), (*_rgb(color), 255))
    tint.putalpha(icon_mask("utensils", inner))
    offset = (side - inner) // 2
    badge.alpha_composite(tint, (offset, offset))
    return badge


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def render(
    url: str,
    color: str = DEFAULT_COLOR,
    logo_file=None,
    box_size: int = 12,
    style: str = DEFAULT_STYLE,
    eyes: str = DEFAULT_EYES,
    center: str = "none",
) -> Image.Image:
    """QR rasmi (RGB).

    `center`: `"logo"` (`logo_file` kerak), `"icon"` (vilka-pichoq) yoki `"none"`.
    """
    if center == "logo" and not logo_file:
        center = "none"
    if center != "none":
        correction = qrcode.constants.ERROR_CORRECT_H
    elif style in ("dots", "gapped"):
        # Nuqtalar modulni to'liq to'ldirmaydi — tuzatish zaxirasi kattaroq bo'lsin.
        correction = qrcode.constants.ERROR_CORRECT_Q
    else:
        correction = qrcode.constants.ERROR_CORRECT_M
    qr = qrcode.QRCode(box_size=box_size, border=2, error_correction=correction)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=STYLES.get(style, STYLES[DEFAULT_STYLE])[1](),
        eye_drawer=EYES.get(eyes, EYES[DEFAULT_EYES])[1](),
        color_mask=SolidFillColorMask(back_color=_rgb(BACKGROUND), front_color=_rgb(color)),
    ).convert("RGBA")

    if center != "none":
        code_side = qr.modules_count * box_size
        side = round(code_side * LOGO_SHARE)
        badge = _logo_badge(logo_file, side) if center == "logo" else _icon_badge(color, side)
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


@dataclass
class RenderedQr:
    image: Image.Image
    #: O'rtada haqiqatan nima chizildi: `logo`, `icon` yoki `none`.
    center: str
    #: Qaysi nuqta shakli ishlatildi — o'qilmagan bo'lsa `square` ga qaytadi.
    style: str
    eyes: str


def restaurant_qr(
    restaurant,
    color: str | None = None,
    center: str | None = None,
    style: str | None = None,
    eyes: str | None = None,
    box_size: int = 12,
) -> RenderedQr:
    """Restoran sozlamalari (yoki oldindan ko'rish uchun berilgan qiymatlar) bilan QR.

    Tanlangan dizayn o'qilmasa, soddaroq variantlar ketma-ket sinab ko'riladi:
    o'rtasi bo'sh, keyin klassik kvadrat. Oxirgisi har doim o'qiladi.
    Logotip tanlangan-u yuklanmagan bo'lsa — vilka-pichoq belgisi.
    """
    url = restaurant.qr_url
    color = color or restaurant.qr_color or DEFAULT_COLOR
    center = center or restaurant.qr_center or DEFAULT_CENTER
    if center == "logo" and not restaurant.logo:
        center = "icon"
    style = style or restaurant.qr_style or DEFAULT_STYLE
    eyes = eyes or restaurant.qr_eyes or DEFAULT_EYES
    logo_file = restaurant.logo if center == "logo" else None

    attempts = [(style, eyes, center)]
    if center != "none":
        attempts.append((style, eyes, "none"))
    if (style, eyes) != (DEFAULT_STYLE, DEFAULT_EYES):
        attempts.append((DEFAULT_STYLE, DEFAULT_EYES, "none"))

    for attempt_style, attempt_eyes, attempt_center in attempts:
        image = render(
            url, color, logo_file, box_size, attempt_style, attempt_eyes, attempt_center
        )
        if decodes_to(image, url):
            return RenderedQr(image, attempt_center, attempt_style, attempt_eyes)
        logger.warning(
            "QR o'qilmadi (%s, %s, center=%s): %s",
            attempt_style,
            attempt_eyes,
            attempt_center,
            restaurant.slug,
        )

    # Klassik qora-oq QR — kutubxona xatosi bo'lmasa bu yerga yetib kelinmaydi.
    image = render(url, DEFAULT_COLOR, None, box_size)
    return RenderedQr(image, "none", DEFAULT_STYLE, DEFAULT_EYES)
