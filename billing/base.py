from dataclasses import dataclass
from typing import Protocol


class PaymentError(RuntimeError):
    """To'lov provayderi javob bermadi yoki so'rovni rad etdi."""


@dataclass(frozen=True)
class ChargeResult:
    """Saqlangan token orqali yechilgan to'lov natijasi (yangilanish/qayta urinish)."""

    provider_payment_id: str
    receipt_url: str = ""


class PaymentProvider(Protocol):
    """To'lov provayderi interfeysi — Payme, Click yoki testlar uchun soxta.

    `handle_webhook` har bir provayderning o'z JSON-RPC/forma shaklidagi
    javobini qaytaradi — Payme va Click protokollari bir xil shaklga
    tushirib bo'lmaydigan darajada farq qiladi. Haqiqiy bazaga yozish
    `menu.billing_ledger.record_payment` orqali — ikkalasi ham o'sha bitta
    joyni chaqiradi, shuning uchun webhook ikki marta kelsa ham bitta marta
    hisoblanadi.
    """

    def create_checkout(self, *, amount: int, description: str, account: dict) -> str:
        """Mijozni to'lov sahifasiga yo'naltiradigan havola."""
        ...

    def charge_token(self, *, token: str, amount: int, description: str) -> ChargeResult:
        """Saqlangan karta tokeni orqali darhol yechish (yangilanish, qayta urinish)."""
        ...

    def handle_webhook(self, request):
        """Provayderdan kelgan so'rovni tekshiradi va javob qaytaradi (`HttpResponse`)."""
        ...
