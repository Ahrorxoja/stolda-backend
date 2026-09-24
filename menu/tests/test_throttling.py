"""So'rov cheklovlari.

Test runner cheklovlarni butun to'plam uchun o'chiradi (aks holda ketma-ket
kirish testlari bir-birini yiqitardi), shuning uchun bu yerda har bir test
kerakli tezlikni o'zi yoqadi va keshni tozalaydi.

DRF tezliklarni `SimpleRateThrottle.THROTTLE_RATES` klass maydoniga import
paytida bog'laydi — `override_settings` unga ta'sir qilmaydi, shuning uchun
aynan shu maydon almashtiriladi.
"""

from functools import wraps

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.throttling import SimpleRateThrottle

from .factories import make_restaurant

LOCMEM = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)


def rates(**scopes):
    """Berilgan scope'lar uchun cheklovni shu test davomida yoqadi."""

    def decorator(func):
        @wraps(func)
        @LOCMEM
        def wrapper(*args, **kwargs):
            previous = SimpleRateThrottle.THROTTLE_RATES
            SimpleRateThrottle.THROTTLE_RATES = {**previous, **scopes}
            cache.clear()
            try:
                return func(*args, **kwargs)
            finally:
                SimpleRateThrottle.THROTTLE_RATES = previous
                cache.clear()

        return wrapper

    return decorator


class PublicViewEventThrottleTests(TestCase):
    """Anonim hodisa — bitta IP statistikani cheksiz shishira olmasin."""

    @rates(views="3/hour")
    def test_too_many_events_from_one_client_are_rejected(self):
        restaurant = make_restaurant()
        url = reverse("public-views", args=[restaurant.slug])

        for _ in range(3):
            self.assertEqual(self.client.post(url, {"kind": "scan"}).status_code, 204)

        self.assertEqual(self.client.post(url, {"kind": "scan"}).status_code, 429)

    @rates(views="1/hour")
    def test_menu_itself_is_never_throttled(self):
        """Cheklov faqat yozishda — menyuni o'qish mijozga to'sib qo'yilmasin."""
        restaurant = make_restaurant()
        self.client.post(reverse("public-views", args=[restaurant.slug]), {"kind": "scan"})

        menu = reverse("public-menu", args=[restaurant.slug])
        for _ in range(5):
            self.assertEqual(self.client.get(menu).status_code, 200)


class LoginThrottleTests(TestCase):
    """Parolni tanlab ko'rishga qarshi."""

    @rates(login="3/min")
    def test_password_guessing_is_cut_off(self):
        get_user_model().objects.create_user(
            username="+998901112233", password="to'g'ri-parol"
        )
        url = reverse("token-obtain")
        wrong = {"phone": "+998901112233", "password": "xato"}

        for _ in range(3):
            self.assertIn(self.client.post(url, wrong).status_code, (400, 401))

        self.assertEqual(self.client.post(url, wrong).status_code, 429)

        # To'g'ri parol ham to'sib qo'yiladi — cheklovni aylanib o'tib bo'lmaydi.
        blocked = self.client.post(
            url, {"phone": "+998901112233", "password": "to'g'ri-parol"}
        )
        self.assertEqual(blocked.status_code, 429)


class ThrottlingIsOffByDefaultInTestsTests(TestCase):
    """Runner cheklovni o'chirgani — qolgan testlar tasodifan yiqilmasligi kafolati."""

    def test_many_events_pass_when_rates_are_disabled(self):
        restaurant = make_restaurant()
        url = reverse("public-views", args=[restaurant.slug])

        for _ in range(30):
            self.assertEqual(self.client.post(url, {"kind": "scan"}).status_code, 204)
