"""Testlar uchun maxsus runner.

Testlar hech qachon tashqi API'ga chiqmasligi kerak: bu pul turadi, sekin va
tarmoqqa bog'liq. Kalitlarni bo'shatamiz — `get_translator()` va `get_bot()`
o'zi soxta provayderga o'tadi.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class StoldaTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.GEMINI_API_KEY = ""
        settings.TELEGRAM_BOT_TOKEN = ""
        settings.TELEGRAM_ADMIN_CHAT_ID = ""
        # Throttle hisobi keshda turadi va testlar orasida tozalanmaydi —
        # o'chirmasak, ketma-ket kirish testlari bir-birini yiqitadi.
        # Cheklovning o'zi `test_throttling.py` da alohida tekshiriladi.
        # DRF tezlikni klass maydoniga import paytida bog'laydi, shuning uchun
        # `settings` emas, aynan o'sha maydon bo'shatiladi.
        from rest_framework.throttling import SimpleRateThrottle

        SimpleRateThrottle.THROTTLE_RATES = {
            scope: None for scope in SimpleRateThrottle.THROTTLE_RATES
        }
