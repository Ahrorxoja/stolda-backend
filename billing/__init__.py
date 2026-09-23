"""To'lov provayderlari — Payme, Click.

`get_provider(name)` sozlamalarga qarab tanlaydi. Kalitlar bo'lmasa
`FakeProvider` qaytadi — shunda dev muhitida va testlarda tashqi so'rov
bo'lmaydi (`translation/` paketi bilan bir xil naqsh).
"""

from .base import ChargeResult, PaymentError, PaymentProvider
from .click import ClickProvider
from .factory import get_provider
from .fake import FakeProvider
from .payme import PaymeProvider

__all__ = [
    "ChargeResult",
    "ClickProvider",
    "FakeProvider",
    "PaymentError",
    "PaymentProvider",
    "PaymeProvider",
    "get_provider",
]
