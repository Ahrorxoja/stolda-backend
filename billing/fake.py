"""Testlar va dev muhiti uchun — tashqi so'rov yubormaydi, natija bashorat qilinadi."""

import json
import secrets

from django.http import JsonResponse

from .base import ChargeResult, PaymentError


class FakeProvider:
    """Kalitlar sozlanmaganda ishlatiladi (`billing/factory.py`).

    `charge_token="tok_fail"` — muvaffaqiyatsiz to'lovni sinash uchun atayin
    xato qaytaradi, boshqa har qanday token muvaffaqiyatli hisoblanadi.
    """

    name = "fake"

    def create_checkout(self, *, amount: int, description: str, account: dict) -> str:
        return f"https://pay.test/{self.name}/checkout?amount={amount}"

    def charge_token(self, *, token: str, amount: int, description: str) -> ChargeResult:
        if token == "tok_fail":
            raise PaymentError("Karta rad etdi (soxta test xatosi).")
        return ChargeResult(provider_payment_id=f"fake_{secrets.token_hex(8)}")

    def handle_webhook(self, request):
        """`{"provider_payment_id": "...", "amount": ..., "restaurant": <id>}`."""
        from .util import get_subscription

        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid json"}, status=400)

        subscription = get_subscription(body.get("restaurant"))
        if subscription is None:
            return JsonResponse({"error": "restaurant not found"}, status=404)

        from menu.billing_ledger import record_payment

        invoice = record_payment(
            subscription,
            provider_payment_id=body["provider_payment_id"],
            amount=body.get("amount", 0),
        )
        return JsonResponse({"invoice": invoice.pk})
