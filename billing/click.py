"""Click — https://docs.click.uz/en/click-api/

Webhook forma-POST orqali keladi (JSON emas), imzo MD5. Ikki bosqich:
`action=0` (Prepare) — to'lovni tasdiqlaydi, `action=1` (Complete) — yakunlaydi.
"""

import hashlib

from django.http import JsonResponse

from .base import ChargeResult, PaymentError
from .util import get_subscription


class ClickProvider:
    name = "click"

    def __init__(self, service_id: str, merchant_id: str, secret_key: str):
        self.service_id = service_id
        self.merchant_id = merchant_id
        self.secret_key = secret_key

    def create_checkout(self, *, amount: int, description: str, account: dict) -> str:
        restaurant_id = account["restaurant_id"]
        return (
            "https://my.click.uz/services/pay"
            f"?service_id={self.service_id}&merchant_id={self.merchant_id}"
            f"&amount={amount}&transaction_param={restaurant_id}"
        )

    def charge_token(self, *, token: str, amount: int, description: str) -> ChargeResult:
        # Click Avtoto'lov (saqlangan karta orqali) alohida API — hali ulanmagan.
        raise PaymentError("Click avtoto'lov hali sozlanmagan.")

    def _sign(self, *parts: str) -> str:
        return hashlib.md5("".join(parts).encode()).hexdigest()

    def handle_webhook(self, request):
        data = request.POST
        action = data.get("action", "")
        click_trans_id = data.get("click_trans_id", "")
        merchant_trans_id = data.get("merchant_trans_id", "")
        amount = data.get("amount", "0")
        sign_time = data.get("sign_time", "")

        if action == "0":
            expected = self._sign(
                click_trans_id, self.service_id, self.secret_key, merchant_trans_id, amount, action, sign_time
            )
        else:
            merchant_prepare_id = data.get("merchant_prepare_id", "")
            expected = self._sign(
                click_trans_id,
                self.service_id,
                self.secret_key,
                merchant_trans_id,
                merchant_prepare_id,
                amount,
                action,
                sign_time,
            )

        if expected != data.get("sign_string"):
            return JsonResponse({"error": -1, "error_note": "SIGN CHECK FAILED!"})

        subscription = get_subscription(merchant_trans_id)
        if subscription is None:
            return JsonResponse({"error": -5, "error_note": "User does not exist"})

        try:
            amount_som = int(round(float(amount)))
        except ValueError:
            return JsonResponse({"error": -2, "error_note": "Incorrect amount"})

        if action == "0":
            from menu.billing_ledger import create_pending_invoice

            invoice = create_pending_invoice(
                subscription,
                provider_payment_id=f"click_{click_trans_id}",
                amount=amount_som,
            )
            return JsonResponse(
                {
                    "click_trans_id": click_trans_id,
                    "merchant_trans_id": merchant_trans_id,
                    "merchant_prepare_id": invoice.pk,
                    "error": 0,
                    "error_note": "Success",
                }
            )

        if action == "1":
            from menu.billing_ledger import record_payment

            invoice = record_payment(
                subscription,
                provider_payment_id=f"click_{click_trans_id}",
                amount=amount_som,
            )
            return JsonResponse(
                {
                    "click_trans_id": click_trans_id,
                    "merchant_trans_id": merchant_trans_id,
                    "merchant_confirm_id": invoice.pk,
                    "error": 0,
                    "error_note": "Success",
                }
            )

        return JsonResponse({"error": -3, "error_note": "Action not found"})
