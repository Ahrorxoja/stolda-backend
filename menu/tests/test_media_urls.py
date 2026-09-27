"""Rasm havolalari brauzer ocha oladigan manzildan yasalishi.

Production'da Next.js menyuni ichki `http://api:8000` orqali oladi. Havola
so'rov xostidan yasalsa `http://api:8000/media/...` bo'lib qolardi va mijoz
menyusida rasmlar ochilmasdi (demo restoranda aniqlangan).
"""

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from menu.models import DishPhoto

from .factories import make_category, make_dish, make_restaurant

MEDIA = tempfile.mkdtemp()
NO_CACHE = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}


def tiny_image(name="taom.webp"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="WEBP")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/webp")


@override_settings(MEDIA_ROOT=MEDIA, CACHES=NO_CACHE, ALLOWED_HOSTS=["api", "testserver"])
class MediaUrlTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.restaurant = make_restaurant()
        dish = make_dish(make_category(self.restaurant))
        DishPhoto.objects.create(dish=dish, image=tiny_image(), position=0)
        self.url = reverse("public-menu", args=[self.restaurant.slug])

    def photo(self, **headers):
        return self.client.get(self.url, **headers).json()["dishes"][0]["photo"]

    @override_settings(MEDIA_BASE_URL="https://stolda.uz")
    def test_internal_host_does_not_leak_into_image_links(self):
        photo = self.photo(HTTP_HOST="api")

        self.assertTrue(photo.startswith("https://stolda.uz/media/"), photo)
        self.assertNotIn("api", photo.split("/")[2])

    @override_settings(MEDIA_BASE_URL="")
    def test_without_base_url_the_request_host_is_used(self):
        """Dev: rasmlarni Django o'zi beradi — so'rov xosti to'g'ri."""
        self.assertTrue(self.photo().startswith("http://testserver/media/"))
