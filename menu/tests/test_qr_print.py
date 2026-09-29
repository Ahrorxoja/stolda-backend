"""Chop etiladigan QR kartochkalari: shablonlar, yozuvlar, fon va skanerlanish."""

import io
import tempfile

import zxingcpp
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from menu import qr_print

from .factories import make_restaurant

MEDIA = tempfile.mkdtemp()


def image_of(response) -> Image.Image:
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def decoded(image: Image.Image) -> list[str]:
    return [result.text for result in zxingcpp.read_barcodes(image)]


@override_settings(MEDIA_ROOT=MEDIA)
class QrPrintTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant(languages=["uz", "ru", "en"], primary_language="uz")
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def get(self, query: str):
        return self.client.get(f"{self.url}qr/?{query}")

    def test_every_template_is_a_pdf_and_its_qr_scans(self):
        for template, (_, width_mm, height_mm) in qr_print.TEMPLATES.items():
            for frame in qr_print.FRAMES:
                with self.subTest(template=template, frame=frame):
                    pdf = self.get(f"format=pdf&template={template}&frame={frame}")
                    self.assertEqual(pdf.status_code, 200)
                    self.assertTrue(pdf.content.startswith(b"%PDF"))
                    self.assertIn(f"-qr-{template}.pdf", pdf["Content-Disposition"])

                    image = image_of(self.get(f"format=image&template={template}&frame={frame}&dpi=150"))
                    # O'lcham shablonniki — chop etganda masshtab to'g'ri chiqadi.
                    self.assertAlmostEqual(image.width / image.height, width_mm / height_mm, places=2)
                    self.assertIn(self.restaurant.qr_url, decoded(image))

    def test_plain_qr_template_has_no_frame_or_text(self):
        self.client.patch(self.url, {"qr_frame": "dark", "qr_color": "#1f4e3d"}, format="json")

        image = image_of(self.get("format=image&template=qr&dpi=150"))

        self.assertEqual(image.width, image.height)
        # To'q fon tanlangan bo'lsa ham — oq, faqat QR rangli.
        self.assertEqual(image.getpixel((1, 1)), (255, 255, 255))
        self.assertIn((0x1F, 0x4E, 0x3D), set(image.getdata()))
        self.assertIn(self.restaurant.qr_url, decoded(image))
        pdf = self.get("format=pdf&template=qr")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("-qr-qr.pdf", pdf["Content-Disposition"])

    def test_old_a6_and_a4_links_still_work(self):
        for fmt, template in (("a6", "card"), ("a4", "poster")):
            response = self.get(f"format={fmt}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"-qr-{template}.pdf", response["Content-Disposition"])

    def test_default_texts_follow_restaurant_languages(self):
        design = qr_print.build_design(self.restaurant)

        self.assertEqual(design.title, "MENYU")
        self.assertEqual(design.hint, "Kamerani qarating")
        self.assertEqual(design.others, "Наведите камеру · Scan with your camera")

    def test_russian_restaurant_gets_russian_title(self):
        self.restaurant.languages = ["ru", "en"]
        self.restaurant.primary_language = "ru"

        design = qr_print.build_design(self.restaurant)

        self.assertEqual((design.title, design.hint), ("МЕНЮ", "Наведите камеру"))
        self.assertEqual(design.others, "Scan with your camera")

    def test_owner_writes_own_texts(self):
        response = self.client.patch(
            self.url,
            {"qr_title": "  Bizning   menyu ", "qr_text": "Skanerlang", "qr_show_link": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["qr_title"], "Bizning menyu")

        self.restaurant.refresh_from_db()
        design = qr_print.build_design(self.restaurant)
        self.assertEqual((design.title, design.hint), ("Bizning menyu", "Skanerlang"))
        # O'z matni yozilgan bo'lsa — avtomatik tarjima qo'shilmaydi.
        self.assertEqual(design.others, "")
        self.assertEqual(design.link, "")

    def test_empty_texts_bring_back_the_defaults(self):
        self.client.patch(self.url, {"qr_title": "X", "qr_text": "Y"}, format="json")
        self.client.patch(self.url, {"qr_title": "", "qr_text": "   "}, format="json")

        self.restaurant.refresh_from_db()
        design = qr_print.build_design(self.restaurant)
        self.assertEqual((design.title, design.hint), ("MENYU", "Kamerani qarating"))

    def test_texts_are_limited_in_length(self):
        response = self.client.patch(self.url, {"qr_title": "M" * 40}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("qr_title", response.data)

    def test_dark_frame_uses_the_qr_color_as_background(self):
        self.client.patch(self.url, {"qr_color": "#1f4e3d", "qr_frame": "dark"}, format="json")

        image = image_of(self.get("format=image&template=card&dpi=80"))

        self.assertEqual(image.getpixel((2, 2)), (0x1F, 0x4E, 0x3D))

    def test_preview_overrides_are_not_saved(self):
        response = self.get("format=image&template=sticker&dpi=80&frame=dark&title=Salom&link=0&inline=1")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.restaurant.refresh_from_db()
        self.assertEqual((self.restaurant.qr_frame, self.restaurant.qr_title), ("light", ""))

    def test_bad_values_are_rejected(self):
        for query in (
            "format=pdf&template=banner",
            "format=pdf",
            "format=image&template=card&frame=neon",
            "format=image&template=card&center=star",
            "format=image&template=card&dpi=abc",
            "format=svg",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.get(query).status_code, 400)

    def test_very_long_name_still_fits_and_scans(self):
        self.restaurant.name = "Juda ham uzun nomli milliy taomlar restorani va kafesi"
        self.restaurant.save()

        image = image_of(self.get("format=image&template=tent&dpi=150"))

        self.assertIn(self.restaurant.qr_url, decoded(image))
