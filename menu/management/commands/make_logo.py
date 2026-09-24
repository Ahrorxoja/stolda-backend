"""stolda.uz logotipi — haqiqiy skanerlanadigan QR kod.

    python manage.py make_logo --out ../stolda/public/brand

Rasm generatorlari (ChatGPT, Gemini) QR'ni faqat o'xshatib chizadi —
skaner o'qimaydi. Bu yerda QR `qrcode` kutubxonasi bilan yasaladi,
xato tuzatish darajasi `H` (30% gacha yo'qotishga chidaydi), shuning
uchun o'rtasini logo bilan yopsa ham o'qiladi.

Burchaklardagi 7×7 belgilar standart nisbatda chiziladi — skaner aynan
shu 1:1:3:1:1 nisbatni qidiradi, uni "chiroyli" qilib o'zgartirib
bo'lmaydi (bir marta shunday qilinib, hech bir skaner o'qimagan edi).
"""

from pathlib import Path

import qrcode
from django.core.management.base import BaseCommand
from qrcode.constants import ERROR_CORRECT_H

AMBER, INK, CREAM, SURFACE = "#C4902A", "#231C17", "#F7F2EC", "#FFFCF8"

UTENSILS = [
    "m16 2-2.3 2.3a3 3 0 0 0 0 4.2l1.8 1.8a3 3 0 0 0 4.2 0L22 8",
    "M15 15 3.3 3.3a4.2 4.2 0 0 0 0 6l7.3 7.3c.7.7 2 .7 2.8 0L15 15Zm0 0 7 7",
    "m2.1 21.8 6.4-6.3",
    "m19 5-7 7",
]

UNIT = 10
QUIET = 4  # QR standarti: kamida 4 modul bo'sh chekka


def finder_cells(size):
    """Uch burchakdagi 7×7 belgilar egallagan kataklar."""
    cells = set()
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(r0, r0 + 7):
            for c in range(c0, c0 + 7):
                cells.add((r, c))
    return cells


def build(data, *, fg, bg, badge, icon, style="dots", badge_ratio=0.15, radius=0.0):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=0, box_size=1)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    skip = finder_cells(size)

    total = (size + QUIET * 2) * UNIT
    off = QUIET * UNIT
    parts = [f'<rect width="{total}" height="{total}" rx="{total * radius:.1f}" fill="{bg}"/>']

    shapes = []
    for row in range(size):
        for col in range(size):
            if not matrix[row][col] or (row, col) in skip:
                continue
            x, y = off + col * UNIT, off + row * UNIT
            if style == "dots":
                shapes.append(
                    f'<circle cx="{x + UNIT / 2:.0f}" cy="{y + UNIT / 2:.0f}" r="{UNIT / 2:.1f}"/>'
                )
            elif style == "rounded":
                shapes.append(
                    f'<rect x="{x:.0f}" y="{y:.0f}" width="{UNIT}" height="{UNIT}" rx="{UNIT * 0.3:.1f}"/>'
                )
            else:
                shapes.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{UNIT}" height="{UNIT}"/>')
    parts.append(f'<g fill="{fg}">{"".join(shapes)}</g>')

    # Burchak belgilari — standart 7×7 / 5×5 / 3×3 nisbati saqlanadi,
    # faqat burchaklari yumshatiladi. Skaner aynan shu nisbatni qidiradi.
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        x, y = off + c0 * UNIT, off + r0 * UNIT
        outer, mid, core = 7 * UNIT, 5 * UNIT, 3 * UNIT
        rx = UNIT * 0.9 if style != "square" else 0
        parts.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{outer}" height="{outer}" rx="{rx:.1f}" fill="{fg}"/>'
            f'<rect x="{x + UNIT:.0f}" y="{y + UNIT:.0f}" width="{mid}" height="{mid}" rx="{rx * 0.7:.1f}" fill="{bg}"/>'
            f'<rect x="{x + 2 * UNIT:.0f}" y="{y + 2 * UNIT:.0f}" width="{core}" height="{core}" rx="{rx * 0.45:.1f}" fill="{fg}"/>'
        )

    # O'rtadagi belgi
    c = total / 2
    br = total * badge_ratio
    parts.append(f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{br * 1.14:.1f}" fill="{bg}"/>')
    parts.append(f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{br:.1f}" fill="{badge}"/>')
    icon_size = br * 1.2
    scale = icon_size / 24
    strokes = "".join(f'<path d="{d}"/>' for d in UTENSILS)
    parts.append(
        f'<g transform="translate({c - icon_size / 2:.1f} {c - icon_size / 2:.1f}) scale({scale:.4f})" '
        f'fill="none" stroke="{icon}" stroke-width="2.2" stroke-linecap="round" '
        f'stroke-linejoin="round">{strokes}</g>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="{total}" height="{total}" role="img" aria-label="stolda.uz">{"".join(parts)}</svg>'
    )



def build_mark(*, fg, bg, badge, icon, radius=0.22):
    """Kichik o'lchamlar uchun soddalashtirilgan belgi.

    24–40px da to'liq QR shunchaki donador kvadratga aylanadi — naqsh ham,
    o'rtadagi pichoq-vilka ham ko'rinmaydi. Bu yerda faqat uchta burchak
    belgisi va o'rtadagi doira qoladi: "QR" degan taassurot saqlanadi,
    lekin favicon o'lchamida ham o'qiladi. Skanerlanmaydi — u vazifa
    to'liq variantda.
    """
    unit, quiet, size = 10, 2, 13
    total = (size + quiet * 2) * unit
    off = quiet * unit
    parts = [f'<rect width="{total}" height="{total}" rx="{total * radius:.1f}" fill="{bg}"/>']

    for r0, c0 in ((0, 0), (0, size - 5), (size - 5, 0)):
        x, y = off + c0 * unit, off + r0 * unit
        outer, core = 5 * unit, 2 * unit
        parts.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{outer}" height="{outer}" '
            f'rx="{unit * 1.5:.1f}" fill="none" stroke="{fg}" stroke-width="{unit * 1.1:.1f}"/>'
            f'<rect x="{x + 1.95 * unit:.0f}" y="{y + 1.95 * unit:.0f}" width="{core}" '
            f'height="{core}" rx="{unit * 0.6:.1f}" fill="{fg}"/>'
        )

    # To'rtinchi burchakda uchta nuqta — QR taassurotini kuchaytiradi.
    for dr, dc in ((0, 0), (0, 2), (2, 0)):
        cx = off + (size - 3 + dc) * unit + unit / 2
        cy = off + (size - 3 + dr) * unit + unit / 2
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{unit * 0.5:.1f}" fill="{fg}"/>')

    c = total / 2
    br = total * 0.2
    parts.append(f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{br * 1.18:.1f}" fill="{bg}"/>')
    parts.append(f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{br:.1f}" fill="{badge}"/>')
    icon_size = br * 1.22
    scale = icon_size / 24
    strokes = "".join(f'<path d="{d}"/>' for d in UTENSILS)
    parts.append(
        f'<g transform="translate({c - icon_size / 2:.1f} {c - icon_size / 2:.1f}) scale({scale:.4f})" '
        f'fill="none" stroke="{icon}" stroke-width="2.4" stroke-linecap="round" '
        f'stroke-linejoin="round">{strokes}</g>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="{total}" height="{total}" role="img" aria-label="stolda.uz">{"".join(parts)}</svg>'
    )


class Command(BaseCommand):
    help = "Brend logotipi: QR ichida pichoq-vilka."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="brand", help="qaysi papkaga")
        parser.add_argument("--url", default="https://stolda.uz", help="QR ichidagi havola")

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        variants = {
            "stolda-qr": dict(fg=AMBER, bg=CREAM, badge=SURFACE, icon=INK, style="rounded", radius=0.16),
            "stolda-qr-dark": dict(fg=CREAM, bg=INK, badge=AMBER, icon=SURFACE, style="rounded", radius=0.16),
            "stolda-qr-mono": dict(fg=INK, bg="#FFFFFF", badge=INK, icon="#FFFFFF", style="rounded", radius=0.16),
            "stolda-qr-sticker": dict(fg=INK, bg="#FFFFFF", badge=AMBER, icon=SURFACE, style="square", radius=0.0),
        }
        for name, colors in variants.items():
            path = out / f"{name}.svg"
            path.write_text(build(options["url"], **colors))
            self.stdout.write(self.style.SUCCESS(f"{path}"))

        # Kichik o'lchamlar uchun — skanerlanmaydi, faqat belgi.
        marks = {
            "stolda-mark": dict(fg=AMBER, bg=CREAM, badge=SURFACE, icon=INK),
            "stolda-mark-dark": dict(fg=CREAM, bg=INK, badge=AMBER, icon=SURFACE),
        }
        for name, colors in marks.items():
            path = out / f"{name}.svg"
            path.write_text(build_mark(**colors))
            self.stdout.write(self.style.SUCCESS(f"{path}"))
