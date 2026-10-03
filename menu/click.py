"""Click SHOP API — Prepare va Complete so'rovlarini qayta ishlash.

Oqim:
1. Egasi `/api/billing/click/` ga davrni yuboradi → `ClickPayment` yaratiladi,
   javobda `my.click.uz/services/pay?...` havolasi qaytadi.
2. Egasi Click'da to'laydi. Click serverimizga ikki so'rov yuboradi:
   - Prepare (`action=0`) — buyurtma bormi, summa to'g'rimi.
   - Complete (`action=1`) — pul yechildi (yoki xato bilan bekor qilindi).
3. Complete muvaffaqiyatli bo'lsa obuna `record_payment` orqali uzayadi.

Click har doim HTTP 200 kutadi; natija `error` maydonida (0 — muvaffaqiyat).
Imzo: `md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
[+ merchant_prepare_id] + amount + action + sign_time)` — qiymatlar Click
yuborganidek, matn ko'rinishida (summa ham "549000.00" bo'lsa shundayligicha).
"""

import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .billing_ledger import record_payment
from .models import ClickPayment, Subscription

logger = logging.getLogger(__name__)

PAY_URL = "https://my.click.uz/services/pay"

# Click xato kodlari (SHOP API hujjatidagi bilan bir xil).
OK = 0
SIGN_FAILED = -1
BAD_AMOUNT = -2
ACTION_NOT_FOUND = -3
ALREADY_PAID = -4
ORDER_NOT_FOUND = -5
TRANSACTION_NOT_FOUND = -6
UPDATE_FAILED = -7
BAD_REQUEST = -8
CANCELED = -9

NOTES = {
    OK: "Success",
    SIGN_FAILED: "SIGN CHECK FAILED!",
    BAD_AMOUNT: "Incorrect parameter amount",
    ACTION_NOT_FOUND: "Action not found",
    ALREADY_PAID: "Already paid",
    ORDER_NOT_FOUND: "Order does not exist",
    TRANSACTION_NOT_FOUND: "Transaction does not exist",
    UPDATE_FAILED: "Failed to update order",
    BAD_REQUEST: "Error in request from click",
    CANCELED: "Transaction cancelled",
}

REQUIRED = (
    "click_trans_id",
    "service_id",
    "merchant_trans_id",
    "amount",
    "action",
    "sign_time",
    "sign_string",
)


def is_enabled() -> bool:
    return bool(settings.CLICK_SERVICE_ID and settings.CLICK_MERCHANT_ID and settings.CLICK_SECRET_KEY)


def pay_url(payment: ClickPayment, return_url: str) -> str:
    """Egasini Click to'lov sahifasiga yuboradigan havola."""
    query = {
        "service_id": settings.CLICK_SERVICE_ID,
        "merchant_id": settings.CLICK_MERCHANT_ID,
        "amount": payment.amount,
        "transaction_param": payment.pk,
        "return_url": return_url,
    }
    return f"{PAY_URL}?{urlencode(query)}"


def sign(data, *, with_prepare_id: bool) -> str:
    parts = [
        data.get("click_trans_id", ""),
        data.get("service_id", ""),
        settings.CLICK_SECRET_KEY,
        data.get("merchant_trans_id", ""),
    ]
    if with_prepare_id:
        parts.append(data.get("merchant_prepare_id", ""))
    parts += [data.get("amount", ""), data.get("action", ""), data.get("sign_time", "")]
    return hashlib.md5("".join(str(part) for part in parts).encode()).hexdigest()


def _reply(data, error: int, **extra) -> dict:
    click_trans_id = _int(data.get("click_trans_id"))
    return {
        # Click bigint kutadi; buzuq qiymat kelsa — qanday kelgan bo'lsa shunday.
        "click_trans_id": data.get("click_trans_id") if click_trans_id is None else click_trans_id,
        "merchant_trans_id": data.get("merchant_trans_id"),
        **extra,
        "error": error,
        "error_note": NOTES[error],
    }


def _check_common(data, *, action: str, with_prepare_id: bool) -> int:
    """Imzo, majburiy maydonlar va harakat turi. 0 — hammasi joyida."""
    if not is_enabled():
        # Kalitlar qo'yilmagan — imzoni tekshirib bo'lmaydi.
        return SIGN_FAILED
    required = REQUIRED + (("merchant_prepare_id",) if with_prepare_id else ())
    if any(not str(data.get(field, "")).strip() for field in required):
        return BAD_REQUEST
    if str(data.get("service_id")) != settings.CLICK_SERVICE_ID:
        return SIGN_FAILED
    if not hmac.compare_digest(sign(data, with_prepare_id=with_prepare_id), str(data.get("sign_string", "")).lower()):
        return SIGN_FAILED
    if str(data.get("action")) != action:
        return ACTION_NOT_FOUND
    return OK


def _amount_matches(data, payment: ClickPayment) -> bool:
    try:
        return Decimal(str(data.get("amount"))) == Decimal(payment.amount)
    except InvalidOperation:
        return False


def _int(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def prepare(data) -> dict:
    """`action=0` — buyurtmani tekshiradi va to'lovga tayyorlaydi."""
    error = _check_common(data, action="0", with_prepare_id=False)
    if error:
        return _reply(data, error)

    order_id = _int(data.get("merchant_trans_id"))
    click_trans_id = _int(data.get("click_trans_id"))
    if order_id is None or click_trans_id is None:
        return _reply(data, ORDER_NOT_FOUND if order_id is None else BAD_REQUEST)

    with transaction.atomic():
        payment = ClickPayment.objects.select_for_update().filter(pk=order_id).first()
        if payment is None:
            return _reply(data, ORDER_NOT_FOUND)
        if payment.status == ClickPayment.Status.PAID:
            return _reply(data, ALREADY_PAID)
        if payment.status == ClickPayment.Status.CANCELED:
            return _reply(data, CANCELED)
        if not _amount_matches(data, payment):
            return _reply(data, BAD_AMOUNT)

        # Oldingi urinish o'tmagan bo'lsa Click yangi tranzaksiya bilan qayta
        # keladi — eng oxirgisini saqlaymiz, Complete aynan shuni tekshiradi.
        payment.click_trans_id = click_trans_id
        payment.click_paydoc_id = _int(data.get("click_paydoc_id"))
        payment.status = ClickPayment.Status.PREPARED
        payment.save(update_fields=["click_trans_id", "click_paydoc_id", "status", "updated_at"])

    return _reply(data, OK, merchant_prepare_id=payment.pk)


def complete(data) -> dict:
    """`action=1` — pul yechildi (yoki Click xato bilan bekor qildi)."""
    error = _check_common(data, action="1", with_prepare_id=True)
    if error:
        return _reply(data, error)

    order_id = _int(data.get("merchant_trans_id"))
    prepare_id = _int(data.get("merchant_prepare_id"))
    click_trans_id = _int(data.get("click_trans_id"))
    if order_id is None:
        return _reply(data, ORDER_NOT_FOUND)

    with transaction.atomic():
        payment = (
            ClickPayment.objects.select_for_update()
            .select_related("subscription__restaurant", "subscription__plan")
            .filter(pk=order_id)
            .first()
        )
        if payment is None:
            return _reply(data, ORDER_NOT_FOUND)
        if prepare_id != payment.pk or click_trans_id != payment.click_trans_id:
            return _reply(data, TRANSACTION_NOT_FOUND)
        if payment.status == ClickPayment.Status.PAID:
            return _reply(data, ALREADY_PAID, merchant_confirm_id=payment.invoice_id or payment.pk)
        if payment.status == ClickPayment.Status.CANCELED:
            return _reply(data, CANCELED)
        if not _amount_matches(data, payment):
            return _reply(data, BAD_AMOUNT)

        # Click o'zi xato yuborsa (karta rad etdi, mablag' yetmadi...) —
        # to'lov bo'lmagan, buyurtmani bekor qilamiz.
        click_error = _int(data.get("error")) or 0
        if click_error < 0:
            payment.status = ClickPayment.Status.CANCELED
            payment.error_note = str(data.get("error_note", ""))[:200]
            payment.save(update_fields=["status", "error_note", "updated_at"])
            return _reply(data, CANCELED)

        try:
            invoice = _mark_paid(payment, click_trans_id)
        except Exception:  # noqa: BLE001 — Click'ga -7 qaytadi, u qayta urinadi
            logger.exception("Click to'lovini yozib bo'lmadi (#%s)", payment.pk)
            transaction.set_rollback(True)
            return _reply(data, UPDATE_FAILED)

    return _reply(data, OK, merchant_confirm_id=invoice.pk)


def _mark_paid(payment: ClickPayment, click_trans_id: int):
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
        # Obuna aynan to'langan davrga o'tadi (chekdagidek — `approve_receipt`).
        if subscription.period != payment.period:
            subscription.period = payment.period
            subscription.save(update_fields=["period"])

        invoice = record_payment(
            subscription,
            provider_payment_id=f"click_{click_trans_id}",
            amount=payment.amount,
        )
        payment.status = ClickPayment.Status.PAID
        payment.invoice = invoice
        payment.save(update_fields=["status", "invoice", "updated_at"])

    subscription.refresh_from_db()
    _after_paid(payment, subscription)
    return invoice


def _after_paid(payment: ClickPayment, subscription: Subscription) -> None:
    """Egasiga va platforma egasiga xabar, soliq cheki — tranzaksiyadan keyin."""
    from .notify import receipt_reviewed
    from .tasks import notify_click_payment, submit_click_fiscal

    until = subscription.current_period_end
    receipt_reviewed(
        subscription.restaurant,
        approved=True,
        until=timezone.localtime(until).strftime("%d.%m.%Y") if until else "",
    )
    transaction.on_commit(lambda: notify_click_payment.delay(payment.pk))
    if settings.CLICK_FISCAL_SPIC:
        transaction.on_commit(lambda: submit_click_fiscal.delay(payment.pk))


# ── Soliq cheki (OFD) ──────────────────────────────────────────────────

FISCAL_URL = "https://api.click.uz/v2/merchant/payment/ofd_data/submit_items"
FISCAL_ITEM_NAME = "stolda.uz — QR menyu xizmati ({period})"


class FiscalError(Exception):
    pass


def merchant_auth_header() -> str:
    """Merchant API: `merchant_user_id:sha1(timestamp + secret_key):timestamp`."""
    timestamp = str(int(timezone.now().timestamp()))
    digest = hashlib.sha1((timestamp + settings.CLICK_SECRET_KEY).encode()).hexdigest()
    return f"{settings.CLICK_MERCHANT_USER_ID}:{digest}:{timestamp}"


def fiscal_payload(payment: ClickPayment) -> dict:
    """Click'ning `ofd_data/submit_items` so'rovi. Summalar tiyinda (×100)."""
    price = payment.amount * 100
    vat_percent = settings.CLICK_FISCAL_VAT_PERCENT
    vat = round(price * vat_percent / (100 + vat_percent)) if vat_percent else 0
    item = {
        "Name": FISCAL_ITEM_NAME.format(period=payment.get_period_display().lower()),
        "SPIC": settings.CLICK_FISCAL_SPIC,
        "PackageCode": settings.CLICK_FISCAL_PACKAGE_CODE,
        "Price": price,
        "Amount": 1,
        "VAT": vat,
        "VATPercent": vat_percent,
        "CommissionInfo": {"TIN": settings.CLICK_FISCAL_TIN},
    }
    return {
        "service_id": int(settings.CLICK_SERVICE_ID),
        "payment_id": payment.click_trans_id,
        "items": [item],
        "received_ecash": price,
        "received_cash": 0,
        "received_card": 0,
    }


def submit_fiscal(payment: ClickPayment) -> None:
    import requests

    try:
        response = requests.post(
            FISCAL_URL,
            json=fiscal_payload(payment),
            headers={"Auth": merchant_auth_header(), "Accept": "application/json"},
            timeout=20,
        )
        body = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as error:
        raise FiscalError(f"Click'ga ulanib bo'lmadi: {error}") from error
    if response.status_code != 200 or body.get("error_code", 0) != 0:
        raise FiscalError(f"HTTP {response.status_code}: {body.get('error_note') or body}")
