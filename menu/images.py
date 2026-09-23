"""Yuklangan rasmlarni siqib, WebP ga o'tkazish."""

import io
from pathlib import Path

from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

MAX_SIDE = 1600
QUALITY = 82


def to_webp(uploaded, max_side: int = MAX_SIDE, quality: int = QUALITY):
    """Rasmni WebP ga siqadi. Rasm bo'lmasa fayl o'zgarishsiz qaytadi."""
    if uploaded is None:
        return uploaded

    try:
        image = Image.open(uploaded)
        image.load()
    except Exception:
        return uploaded

    # EXIF burilishini hisobga olamiz, shaffoflikni saqlaymiz.
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
    image.thumbnail((max_side, max_side), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=6)
    buffer.seek(0)

    name = f"{Path(getattr(uploaded, 'name', 'image')).stem}.webp"
    return InMemoryUploadedFile(
        buffer, "ImageField", name, "image/webp", buffer.getbuffer().nbytes, None
    )
