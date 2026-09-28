"""Chop etiladigan QR kartochkalari: stol tenti, kartochka (A6), stiker, poster (A4).

Oddiy QR "bu nima?" degan savolga javob bermaydi — mijoz uni Wi-Fi yoki
reklama deb o'ylaydi. Shuning uchun QR doim ramka ichida: katta "MENYU"
sarlavhasi, "Kamerani qarating" izohi (restoran tillarida), restoran nomi.

Hammasi Pillow bilan bitta joyda chiziladi: PDF, PNG va admin paneldagi
jonli ko'rinish bir xil. Chop etish uchun 300 DPI, ko'rinish uchun kamroq.
"""

import io
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.lib.units import mm as MM
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from . import qr as qr_codes

PRINT_DPI = 300

#: Shablon → (nomi, eni mm, bo'yi mm).
TEMPLATES = {
    "tent": ("Stol tenti", 210, 297),
    "card": ("Kartochka A6", 105, 148),
    "sticker": ("Stiker 8×8", 80, 80),
    "poster": ("Poster A4", 210, 297),
}
FRAMES = ("light", "dark")

#: Standart yozuvlar. Shrift faqat lotin va kirillni qamraydi — boshqa
#: tillar (xitoy, koreys, arab) uchun inglizcha ishlatiladi.
TEXTS = {
    "uz": ("MENYU", "Kamerani qarating"),
    "uz-Cyrl": ("МЕНЮ", "Камерани қаратинг"),
    "ru": ("МЕНЮ", "Наведите камеру"),
    "en": ("MENU", "Scan with your camera"),
    "tr": ("MENÜ", "Kamerayla okutun"),
    "de": ("MENÜ", "Mit der Kamera scannen"),
}

INK = (35, 28, 23)
MUTED = (122, 110, 100)
CREAM = (251, 247, 241)
WHITE = (255, 255, 255)


def default_texts(lang: str) -> tuple[str, str]:
    return TEXTS.get(lang, TEXTS["en"])


@dataclass
class Design:
    """Kartochkada nima chiqadi — restoran sozlamalari + oldindan ko'rish qiymatlari."""

    name: str
    title: str
    hint: str
    #: Boshqa tillardagi izoh — faqat standart matnda (egasi o'zgartirmagan bo'lsa).
    others: str
    link: str
    frame: str
    color: str
    header_logo: Image.Image | None

    @property
    def accent(self) -> tuple[int, int, int]:
        return tuple(int(self.color[i : i + 2], 16) for i in (1, 3, 5))

    @property
    def dark(self) -> bool:
        return self.frame == "dark"

    @property
    def background(self):
        return self.accent if self.dark else CREAM

    @property
    def text(self):
        return WHITE if self.dark else INK

    @property
    def title_color(self):
        return WHITE if self.dark else self.accent

    @property
    def muted(self):
        return (255, 255, 255, 190) if self.dark else MUTED

    @property
    def border(self):
        return (255, 255, 255, 90) if self.dark else (*self.accent, 110)


def build_design(restaurant, *, frame=None, title=None, text=None, link=None, color=None, center=None) -> Design:
    lang = restaurant.primary_language
    default_title, default_hint = default_texts(lang)
    custom_title = restaurant.qr_title if title is None else title
    custom_text = restaurant.qr_text if text is None else text
    hint = (custom_text or "").strip() or default_hint

    others = ""
    if not (custom_text or "").strip():
        seen = {default_hint}
        extra = []
        for code in restaurant.languages or []:
            if code == lang or code not in TEXTS:
                continue
            candidate = TEXTS[code][1]
            if candidate not in seen:
                seen.add(candidate)
                extra.append(candidate)
        others = " · ".join(extra[:2])

    show_link = restaurant.qr_show_link if link is None else link
    center = center or restaurant.qr_center
    header_logo = None
    # Logotip QR o'rtasida bo'lmasa — tepada, restoran nomi yonida.
    if restaurant.logo and center != "logo":
        header_logo = _open_image(restaurant.logo)

    return Design(
        name=restaurant.name,
        title=((custom_title or "").strip() or default_title),
        hint=hint,
        others=others,
        link=restaurant.qr_url.split("://", 1)[-1] if show_link else "",
        frame=frame or restaurant.qr_frame or "light",
        color=color or restaurant.qr_color or qr_codes.DEFAULT_COLOR,
        header_logo=header_logo,
    )


def _open_image(field) -> Image.Image | None:
    try:
        field.open("rb")
        with Image.open(field) as source:
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, ValueError):
        return None
    finally:
        try:
            field.close()
        except (OSError, ValueError):
            pass


# ── Chizish yordamchilari ──────────────────────────────────────────────


@lru_cache(maxsize=64)
def _font(family: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(qr_codes.ASSETS / f"{family}.ttf"), size)
    font.set_variation_by_axes([weight])
    return font


class Canvas:
    """Millimetrda ishlaydigan kichik yordamchi — DPI dan mustaqil maket."""

    def __init__(self, width_mm: float, height_mm: float, dpi: int, background):
        self.dpi = dpi
        self.image = Image.new(
            "RGBA", (self.px(width_mm), self.px(height_mm)), (*background, 255)
        )
        self.draw = ImageDraw.Draw(self.image)

    def px(self, value_mm: float) -> int:
        return round(value_mm / 25.4 * self.dpi)

    def font(self, family: str, size_mm: float, weight: int):
        return _font(family, max(self.px(size_mm), 6), weight)

    def text_width(self, text: str, font, tracking: float = 0) -> float:
        return font.getlength(text) + tracking * font.size * max(len(text) - 1, 0)

    def fit(self, text: str, family: str, size_mm: float, weight: int, max_width_mm: float, tracking=0):
        """Sig'guncha kichraytiriladi — uzun nom yoki matn chetdan chiqmasin."""
        size = size_mm
        font = self.font(family, size, weight)
        while size > 1.5 and self.text_width(text, font, tracking) > self.px(max_width_mm):
            size *= 0.94
            font = self.font(family, size, weight)
        return font

    def text(self, cx_mm: float, top_mm: float, text: str, font, fill, tracking: float = 0) -> float:
        """Markazga tekislangan matn; pastki chetini (mm) qaytaradi."""
        if not text:
            return top_mm
        width = self.text_width(text, font, tracking)
        x = self.px(cx_mm) - width / 2
        y = self.px(top_mm)
        ascent, descent = font.getmetrics()
        layer = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if tracking:
            for char in text:
                draw.text((x, y), char, font=font, fill=fill)
                x += font.getlength(char) + tracking * font.size
        else:
            draw.text((x, y), text, font=font, fill=fill)
        self.image.alpha_composite(layer)
        return top_mm + (ascent + descent) / self.dpi * 25.4

    def rounded(self, box_mm, radius_mm, fill=None, outline=None, width_mm=0.0):
        x0, y0, x1, y1 = (self.px(v) for v in box_mm)
        layer = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            (x0, y0, x1, y1),
            radius=self.px(radius_mm),
            fill=fill,
            outline=outline,
            width=max(self.px(width_mm), 1) if outline else 0,
        )
        self.image.alpha_composite(layer)

    def paste(self, image: Image.Image, left_mm: float, top_mm: float, size_mm: float, circle=False):
        side = self.px(size_mm)
        tile = ImageOps.fit(image.convert("RGBA"), (side, side), Image.Resampling.LANCZOS)
        if circle:
            mask = Image.new("L", (side, side), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
            tile.putalpha(Image.composite(tile.getchannel("A"), mask, mask))
        self.image.alpha_composite(tile, (self.px(left_mm), self.px(top_mm)))

    def icon(self, name: str, left_mm: float, top_mm: float, size_mm: float, color):
        side = self.px(size_mm)
        tint = Image.new("RGBA", (side, side), color if len(color) == 4 else (*color, 255))
        mask = qr_codes.icon_mask(name, side)
        if len(color) == 4:
            mask = mask.point(lambda value: value * color[3] // 255)
        tint.putalpha(mask)
        self.image.alpha_composite(tint, (self.px(left_mm), self.px(top_mm)))


def _qr_card(canvas: Canvas, qr_image: Image.Image, left: float, top: float, side: float):
    """Oq kartochka ichidagi QR — to'q fonda ham QR oq ustida qoladi."""
    canvas.rounded((left, top, left + side, top + side), side * 0.07, fill=(255, 255, 255, 255))
    inset = side * 0.045
    inner = canvas.px(side - inset * 2)
    code = qr_image.resize((inner, inner), Image.Resampling.LANCZOS)
    canvas.image.paste(code, (canvas.px(left + inset), canvas.px(top + inset)))


def _hint_row(canvas: Canvas, design: Design, cx: float, top: float, size: float, max_width: float) -> float:
    """Skaner belgisi + "Kamerani qarating"."""
    font = canvas.fit(design.hint, "Rubik", size, 500, max_width - size * 1.6)
    icon = size * 1.15
    gap = size * 0.45
    text_w = canvas.text_width(design.hint, font) / canvas.dpi * 25.4
    total = icon + gap + text_w
    left = cx - total / 2
    canvas.icon("scan", left, top - size * 0.05, icon, design.title_color)
    return canvas.text(left + icon + gap + text_w / 2, top, design.hint, font, design.text)


def _header(canvas: Canvas, design: Design, cx: float, top: float, size: float, max_width: float):
    """Restoran nomi (logotipi bo'lsa — yonida)."""
    logo = design.header_logo
    logo_size = size * 1.9 if logo else 0
    gap = size * 0.55 if logo else 0
    font = canvas.fit(design.name, "Rubik", size, 600, max_width - logo_size - gap)
    text_w = canvas.text_width(design.name, font) / canvas.dpi * 25.4
    left = cx - (logo_size + gap + text_w) / 2
    if logo:
        canvas.paste(logo, left, top - (logo_size - size * 1.25) / 2, logo_size, circle=True)
    canvas.text(left + logo_size + gap + text_w / 2, top, design.name, font, design.text)


# ── Shablonlar ─────────────────────────────────────────────────────────


def _portrait(canvas: Canvas, design: Design, qr_image: Image.Image, width: float, height: float):
    """Kartochka (A6) va poster (A4) — bir xil nisbat, o'lcham `s` bilan."""
    s = width / 105
    cx = width / 2
    inner_w = width - 18 * s
    canvas.rounded((4 * s, 4 * s, width - 4 * s, height - 4 * s), 5 * s, outline=design.border, width_mm=0.5 * s)

    _header(canvas, design, cx, 11 * s, 4.3 * s, inner_w)
    title_font = canvas.fit(design.title, "PlayfairDisplay", 14 * s, 500, inner_w, tracking=0.12)
    canvas.text(cx, 20 * s, design.title, title_font, design.title_color, tracking=0.12)

    side = 64 * s
    _qr_card(canvas, qr_image, cx - side / 2, 43 * s, side)

    bottom = _hint_row(canvas, design, cx, 113 * s, 5 * s, inner_w)
    if design.others:
        font = canvas.fit(design.others, "Rubik", 3.3 * s, 400, inner_w)
        canvas.text(cx, bottom + 1.8 * s, design.others, font, design.muted)
    if design.link:
        canvas.text(cx, height - 12 * s, design.link, canvas.fit(design.link, "Rubik", 3.2 * s, 500, inner_w), design.muted)


def _sticker(canvas: Canvas, design: Design, qr_image: Image.Image, width: float, height: float):
    """Kvadrat stiker — faqat eng kerakli: sarlavha, QR, izoh."""
    s = width / 80
    cx = width / 2
    canvas.rounded((2.5 * s, 2.5 * s, width - 2.5 * s, height - 2.5 * s), 5 * s, outline=design.border, width_mm=0.45 * s)
    title_font = canvas.fit(design.title, "PlayfairDisplay", 8.5 * s, 500, width - 16 * s, tracking=0.12)
    canvas.text(cx, 5.5 * s, design.title, title_font, design.title_color, tracking=0.12)
    side = 49 * s
    _qr_card(canvas, qr_image, cx - side / 2, 18.5 * s, side)
    _hint_row(canvas, design, cx, 70.5 * s, 3.6 * s, width - 12 * s)


def _tent_face(canvas: Canvas, design: Design, qr_image: Image.Image, width: float, height: float):
    """Tentning bir yuzi (landshaft): chapda QR, o'ngda yozuvlar."""
    canvas.rounded((6, 6, width - 6, height - 6), 6, outline=design.border, width_mm=0.6)
    side = 88
    top = (height - side) / 2
    _qr_card(canvas, qr_image, 16, top, side)

    column_left = 16 + side + 8
    column_w = width - column_left - 12
    cx = column_left + column_w / 2
    _header(canvas, design, cx, top + 3, 5.4, column_w)
    title_font = canvas.fit(design.title, "PlayfairDisplay", 19, 500, column_w, tracking=0.1)
    canvas.text(cx, top + 15, design.title, title_font, design.title_color, tracking=0.1)
    bottom = _hint_row(canvas, design, cx, top + 47, 6, column_w)
    if design.others:
        canvas.text(cx, bottom + 2.4, design.others, canvas.fit(design.others, "Rubik", 3.9, 400, column_w), design.muted)
    if design.link:
        canvas.text(cx, top + side - 6, design.link, canvas.fit(design.link, "Rubik", 3.8, 500, column_w), design.muted)


def render_template(restaurant, template: str, *, dpi: int = PRINT_DPI, qr_options=None, design_options=None) -> Image.Image:
    """Shablonni RGB rasm sifatida chizadi. `*_options` — oldindan ko'rish qiymatlari."""
    qr_options = qr_options or {}
    design_options = design_options or {}
    rendered = qr_codes.restaurant_qr(restaurant, box_size=24, **qr_options)
    design = build_design(
        restaurant,
        color=qr_options.get("color"),
        center=rendered.center,
        **design_options,
    )
    _, width, height = TEMPLATES[template]

    if template == "tent":
        face_h = height / 2
        face = Canvas(width, face_h, dpi, design.background)
        _tent_face(face, design, rendered.image, width, face_h)
        sheet = Canvas(width, height, dpi, design.background)
        # Buklanganda ikkala tomondan ham to'g'ri o'qilsin — ustki yuz teskari.
        sheet.image.alpha_composite(face.image.rotate(180), (0, 0))
        sheet.image.alpha_composite(face.image, (0, sheet.px(face_h)))
        _fold_line(sheet, width, face_h, design)
        return sheet.image.convert("RGB")

    canvas = Canvas(width, height, dpi, design.background)
    if template == "sticker":
        _sticker(canvas, design, rendered.image, width, height)
    else:
        _portrait(canvas, design, rendered.image, width, height)
    return canvas.image.convert("RGB")


def _fold_line(canvas: Canvas, width: float, y: float, design: Design):
    """Buklash chizig'i — punktir."""
    color = (255, 255, 255, 140) if design.dark else (*MUTED, 150)
    layer = Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    py = canvas.px(y)
    dash, gap = canvas.px(3), canvas.px(2)
    x = canvas.px(4)
    while x < canvas.px(width - 4):
        draw.line((x, py, min(x + dash, canvas.px(width - 4)), py), fill=color, width=max(canvas.px(0.3), 1))
        x += dash + gap
    canvas.image.alpha_composite(layer)


def render_pdf(restaurant, template: str, **options) -> bytes:
    """Chop etish uchun PDF — sahifa o'lchami shablonning aniq o'lchamida."""
    image = render_template(restaurant, template, **options)
    _, width, height = TEMPLATES[template]
    buffer = io.BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=(width * MM, height * MM))
    canvas.setTitle(f"{restaurant.name} — {TEMPLATES[template][0]}")
    canvas.drawImage(ImageReader(image), 0, 0, width * MM, height * MM)
    canvas.save()
    return buffer.getvalue()
