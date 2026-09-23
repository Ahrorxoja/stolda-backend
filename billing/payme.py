"""Payme Merchant API — https://developer.help.paycom.uz/

JSON-RPC 2.0, bitta endpoint. Webhook autentifikatsiyasi HTTP Basic
(`Paycom:<PAYME_KEY>`), karta raqami emas. Pul miqdori tiyinda (1 so'm = 100 tiyin).
"""

import base64
import json

from django.http import JsonResponse
from django.utils import timezone

from .base import ChargeResult, PaymentError
from .util import get_subscription


def _ms(dt) -> int:
    return int(dt.timestamp() * 1000) if dt else 0


class PaymeProvider:
    name = "payme"

    def __init__(self, merchant_id: str, key: str):
        self.merchant_id = merchant_id
        self.key = key

    def create_checkout(self, *, amount: int, description: str, account: dict) -> str:
        restaurant_id = account["restaurant_id"]
        tiyin = amount * 100
        raw = f"m={self.merchant_id};ac.restaurant_id={restaurant_id};a={tiyin}"
        encoded = base64.b64encode(raw.encode()).decode()
        return f"https://checkout.paycom.uz/{encoded}"

    def charge_token(self, *, token: str, amount: int, description: str) -> ChargeResult:
        # Payme Cards.Receipts (saqlangan karta) alohida API — hali ulanmagan.
        raise PaymentError("Payme avtoto'lov hali sozlanmagan.")

    def _authorized(self, request) -> bool:
        header = request.META.get("HTTP_AUTHORIZATION", "")
        expected = "Basic " + base64.b64encode(f"Paycom:{self.key}".encode()).decode()
        return bool(self.key) and header == expected

    def handle_webhook(self, request):
        if not self._authorized(request):
            return JsonResponse({"error": {"code": -32504, "message": "Ruxsat yo'q."}})

        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": {"code": -32700, "message": "Noto'g'ri JSON."}})

        request_id = payload.get("id")
        method = payload.get("method", "")
        params = payload.get("params", {})
        handler = getattr(self, f"_rpc_{method}", None)
        if handler is None:
            return self._error(request_id, -32601, "Metod topilmadi.")
        return handler(request_id, params)

    def _result(self, request_id, result):
        return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id, code, message):
        return JsonResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    def _rpc_CheckPerformTransaction(self, request_id, params):
        subscription = get_subscription(params.get("account", {}).get("restaurant_id"))
        if subscription is None:
            return self._error(request_id, -31050, "Restoran topilmadi.")
        return self._result(request_id, {"allow": True})

    def _rpc_CreateTransaction(self, request_id, params):
        from menu.billing_ledger import create_pending_invoice

        subscription = get_subscription(params.get("account", {}).get("restaurant_id"))
        if subscription is None:
            return self._error(request_id, -31050, "Restoran topilmadi.")

        payme_id = params["id"]
        amount_som = params.get("amount", 0) // 100
        invoice = create_pending_invoice(
            subscription, provider_payment_id=payme_id, amount=amount_som
        )
        return self._result(
            request_id,
            {"create_time": _ms(invoice.created_at), "transaction": str(invoice.pk), "state": 1},
        )

    def _rpc_PerformTransaction(self, request_id, params):
        from menu.models import Invoice
        from menu.billing_ledger import record_payment

        payme_id = params["id"]
        invoice = (
            Invoice.objects.filter(provider_payment_id=payme_id)
            .select_related("subscription")
            .first()
        )
        if invoice is None:
            return self._error(request_id, -31003, "Tranzaksiya topilmadi.")
        invoice = record_payment(
            invoice.subscription, provider_payment_id=payme_id, amount=invoice.amount
        )
        return self._result(
            request_id,
            {"transaction": str(invoice.pk), "perform_time": _ms(invoice.paid_at), "state": 2},
        )

    def _rpc_CancelTransaction(self, request_id, params):
        from menu.models import Invoice

        payme_id = params["id"]
        invoice = Invoice.objects.filter(provider_payment_id=payme_id).first()
        if invoice is None:
            return self._error(request_id, -31003, "Tranzaksiya topilmadi.")
        if invoice.status != Invoice.Status.PAID:
            invoice.status = Invoice.Status.FAILED
            invoice.save(update_fields=["status"])
        return self._result(
            request_id,
            {"transaction": str(invoice.pk), "cancel_time": _ms(timezone.now()), "state": -1},
        )

    def _rpc_CheckTransaction(self, request_id, params):
        from menu.models import Invoice

        payme_id = params["id"]
        invoice = Invoice.objects.filter(provider_payment_id=payme_id).first()
        if invoice is None:
            return self._error(request_id, -31003, "Tranzaksiya topilmadi.")
        state = {"pending": 1, "paid": 2, "failed": -1, "refunded": -2}[invoice.status]
        return self._result(
            request_id,
            {
                "create_time": _ms(invoice.created_at),
                "perform_time": _ms(invoice.paid_at),
                "cancel_time": 0,
                "transaction": str(invoice.pk),
                "state": state,
            },
        )
