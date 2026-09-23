"""Testlar uchun maxsus runner.

Testlar hech qachon haqiqiy Gemini API'ga chiqmasligi kerak: bu pul turadi,
sekin va tarmoqqa bog'liq. Shuning uchun kalitni bo'shatamiz — `get_translator()`
o'zi `FakeTranslator` ga o'tadi.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class StoldaTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.GEMINI_API_KEY = ""
