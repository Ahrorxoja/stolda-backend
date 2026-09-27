"""QR dizayni: rang, o'rtada logotip va skanerlanish kafolati."""

import io
import tempfile

import zxingcpp
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from menu import qr
from menu.models import Restaurant

from .factories import make_restaurant

MEDIA = tempfile.mkdtemp()


def logo_file(color="#C4902A", size=400) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    image = Image.new("RGB", (size, size), color)
    # Logotipda ham QR'ga o'xshash dog'lar bo'lsin — eng og'ir holat.
    for x in range(0, size, 40):
        for y in range(0, size, 40):
            if (x + y) // 40 % 2:
                image.paste((20, 20, 20), (x, y, x + 20, y + 20))
    image.save(buffer, format="PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")


def decoded(content: bytes) -> list[str]:
    return [result.text for result in zxingcpp.read_barcodes(Image.open(io.BytesIO(content)))]


def pixel_colors(content: bytes) -> set:
    return set(Image.open(io.BytesIO(content)).convert("RGB").getdata())


class ColorRulesTests(TestCase):
    def test_dark_colors_pass_and_are_normalized(self):
        self.assertEqual(qr.normalize_color("7A1F2B"), "#7a1f2b")
        self.assertEqual(qr.normalize_color(" #231C17 "), "#231c17")

    def test_light_colors_are_rejected(self):
        for color in ("#FFFFFF", "#F4E8D4", "#C4902A", "#FFD700"):
            with self.subTest(color=color), self.assertRaises(qr.QrColorError):
                qr.normalize_color(color)

    def test_garbage_is_rejected(self):
        for color in ("", "red", "#12345", "#GGGGGG", "#1234567"):
            with self.subTest(color=color), self.assertRaises(qr.QrColorError):
                qr.normalize_color(color)


@override_settings(MEDIA_ROOT=MEDIA)
class QrStyleApiTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def qr_png(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(f"{self.url}qr/?format=png&{query}")

    def test_default_qr_is_dark_and_scans(self):
        response = self.qr_png()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(decoded(response.content), [self.restaurant.qr_url])
        self.assertIn((0x23, 0x1C, 0x17), pixel_colors(response.content))
        self.assertEqual(response["X-QR-Logo"], "0")

    def test_owner_saves_a_color_and_downloads_use_it(self):
        response = self.client.patch(self.url, {"qr_color": "7A1F2B"}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["qr_color"], "#7a1f2b")
        png = self.qr_png()
        self.assertIn((0x7A, 0x1F, 0x2B), pixel_colors(png.content))
        self.assertEqual(decoded(png.content), [self.restaurant.qr_url])
        for fmt in ("a6", "a4"):
            pdf = self.client.get(f"{self.url}qr/?format={fmt}")
            self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_light_color_is_not_saved(self):
        response = self.client.patch(self.url, {"qr_color": "#F4E8D4"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("qr_color", response.data)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.qr_color, "#231c17")

    def test_logo_needs_an_uploaded_logo(self):
        response = self.client.patch(self.url, {"qr_logo": True}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("qr_logo", response.data)

    def test_logo_in_the_middle_still_scans(self):
        self.client.patch(self.url, {"logo": logo_file()}, format="multipart")
        response = self.client.patch(self.url, {"qr_logo": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        png = self.qr_png()

        self.assertEqual(png["X-QR-Logo"], "1")
        self.assertEqual(decoded(png.content), [self.restaurant.qr_url])
        # Logotipning oltinrang qismi QR ichida ko'rinadi.
        self.assertTrue(any(abs(r - 0xC4) < 12 and abs(g - 0x90) < 12 for r, g, _ in pixel_colors(png.content)))

    def test_preview_overrides_without_saving(self):
        response = self.qr_png(color="%231F3A5F", inline=1)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertIn((0x1F, 0x3A, 0x5F), pixel_colors(response.content))
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.qr_color, "#231c17")

    def test_preview_rejects_light_color(self):
        self.assertEqual(self.qr_png(color="%23FFFFFF").status_code, 400)

    def test_logo_that_breaks_scanning_falls_back_to_plain_qr(self):
        self.client.patch(self.url, {"logo": logo_file()}, format="multipart")
        Restaurant.objects.filter(pk=self.restaurant.pk).update(qr_logo=True)

        original = qr.LOGO_SHARE
        qr.LOGO_SHARE = 0.6  # QR'ning katta qismini yopadi — o'qib bo'lmaydi
        try:
            png = self.qr_png()
        finally:
            qr.LOGO_SHARE = original

        self.assertEqual(png["X-QR-Logo"], "0")
        self.assertEqual(decoded(png.content), [self.restaurant.qr_url])


@override_settings(MEDIA_ROOT=MEDIA)
class QrShapeTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.client = APIClient()
        self.client.force_authenticate(self.restaurant.owner)
        self.url = f"/api/restaurants/{self.restaurant.pk}/"

    def test_every_shape_and_corner_combination_scans(self):
        self.client.patch(self.url, {"logo": logo_file()}, format="multipart")
        for style in qr.STYLES:
            for eyes in qr.EYES:
                for logo in (0, 1):
                    with self.subTest(style=style, eyes=eyes, logo=logo):
                        response = self.client.get(
                            f"{self.url}qr/?format=png&style={style}&eyes={eyes}&logo={logo}"
                        )
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response["X-QR-Style"], f"{style}:{eyes}")
                        self.assertEqual(response["X-QR-Logo"], str(logo))
                        self.assertEqual(decoded(response.content), [self.restaurant.qr_url])

    def test_shape_is_saved_and_used_for_downloads(self):
        response = self.client.patch(
            self.url, {"qr_style": "dots", "qr_eyes": "rounded"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["qr_style"], response.data["qr_eyes"]), ("dots", "rounded"))
        png = self.client.get(f"{self.url}qr/?format=png")
        self.assertEqual(png["X-QR-Style"], "dots:rounded")

    def test_unknown_shapes_are_rejected(self):
        self.assertEqual(
            self.client.patch(self.url, {"qr_style": "stars"}, format="json").status_code, 400
        )
        self.assertEqual(
            self.client.patch(self.url, {"qr_eyes": "hearts"}, format="json").status_code, 400
        )
        self.assertEqual(self.client.get(f"{self.url}qr/?format=png&style=stars").status_code, 400)
        self.assertEqual(self.client.get(f"{self.url}qr/?format=png&eyes=x").status_code, 400)

    def test_unreadable_shape_falls_back_to_classic(self):
        Restaurant.objects.filter(pk=self.restaurant.pk).update(qr_style="dots")
        real = qr.decodes_to
        attempts = []

        def fail_first(image, url):
            # Birinchi urinish (nuqtali) "o'qilmadi", keyingisi haqiqiy tekshiruv.
            attempts.append(image)
            return len(attempts) > 1 and real(image, url)

        qr.decodes_to = fail_first
        try:
            png = self.client.get(f"{self.url}qr/?format=png")
        finally:
            qr.decodes_to = real

        self.assertEqual(png["X-QR-Style"], "square:square")
        self.assertEqual(decoded(png.content), [self.restaurant.qr_url])
