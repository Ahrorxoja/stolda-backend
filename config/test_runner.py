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
        settings.GEMINI_API_KEYS = ""
        settings.TELEGRAM_BOT_TOKEN = ""
        settings.TELEGRAM_ADMIN_CHAT_ID = ""
        settings.AGENT_BOT_TOKEN = ""
        # Haqiqiy parol xeshi ataylab sekin (PBKDF2, ~1 mln iteratsiya) — har
        # `create_user` da soniyaning bir qismi ketardi va 450+ test 6 daqiqa
        # yurardi. Testlarda xavfsizlik kerak emas, tezlik kerak.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
        from django.contrib.auth.hashers import get_hashers, get_hashers_by_algorithm

        get_hashers.cache_clear()
        get_hashers_by_algorithm.cache_clear()
        # Throttle hisobi keshda turadi va testlar orasida tozalanmaydi —
        # o'chirmasak, ketma-ket kirish testlari bir-birini yiqitadi.
        # Cheklovning o'zi `test_throttling.py` da alohida tekshiriladi.
        # DRF tezlikni klass maydoniga import paytida bog'laydi, shuning uchun
        # `settings` emas, aynan o'sha maydon bo'shatiladi.
        from rest_framework.throttling import SimpleRateThrottle

        SimpleRateThrottle.THROTTLE_RATES = {
            scope: None for scope in SimpleRateThrottle.THROTTLE_RATES
        }
