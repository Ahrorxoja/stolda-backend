from django.conf import settings

from .base import PaymentProvider
from .click import ClickProvider
from .fake import FakeProvider
from .payme import PaymeProvider


def get_provider(name: str) -> PaymentProvider:
    """Sozlamalarga qarab haqiqiy yoki soxta provayderni qaytaradi.

    Kalitlar bo'lmasa `FakeProvider` — `get_translator()` bilan bir xil
    naqsh: dev muhitida va testlarda tashqi so'rov yuborilmaydi.
    """
    if name == "payme":
        merchant_id = getattr(settings, "PAYME_MERCHANT_ID", "")
        key = getattr(settings, "PAYME_KEY", "")
        if not merchant_id or not key:
            return FakeProvider()
        return PaymeProvider(merchant_id, key)

    if name == "click":
        service_id = getattr(settings, "CLICK_SERVICE_ID", "")
        merchant_id = getattr(settings, "CLICK_MERCHANT_ID", "")
        secret_key = getattr(settings, "CLICK_SECRET_KEY", "")
        if not service_id or not merchant_id or not secret_key:
            return FakeProvider()
        return ClickProvider(service_id, merchant_id, secret_key)

    raise ValueError(f"Noma'lum to'lov provayderi: {name}")
