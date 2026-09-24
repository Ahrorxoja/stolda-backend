"""Chek tasdiqlanganda bazani yangilaydigan yagona joy.

To'lov qo'lda: restoran egasi chek yuklaydi, platforma egasi Telegram'dan
tasdiqlaydi. Tasdiqlash ikki marta bosilsa ham obuna bir marta uzaytiriladi
(`Invoice.provider_payment_id` unique).
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .cache import bump_menu_version
from .models import Invoice, PaymentReceipt, Subscription

PERIOD_DAYS = {Subscription.Period.MONTH: 30, Subscription.Period.YEAR: 365}


@transaction.atomic
def record_payment(
    subscription: Subscription,
    *,
    provider_payment_id: str,
    amount: int,
    receipt: PaymentReceipt | None = None,
) -> Invoice:
    """To'lovni yozadi va obunani darhol tiklaydi/uzaytiradi.

    Idempotent — bir xil `provider_payment_id` bilan ikkinchi chaqiruv hech
    narsani o'zgartirmaydi.
    """
    invoice = (
        Invoice.objects.select_for_update()
        .filter(provider_payment_id=provider_payment_id)
        .first()
    )
    if invoice is not None and invoice.status == Invoice.Status.PAID:
        return invoice

    now = timezone.now()
    # Muddat tugamasdan to'lansa, qolgan kunlar kuymasligi kerak — yangi davr
    # joriy davrning oxiridan boshlanadi. Muddat o'tib ketgan bo'lsa (past_due,
    # suspended) hisob bugundan yuritiladi, aks holda to'liq davr berilmasdi.
    starts_at = max(now, subscription.current_period_end or now)
    period_end = starts_at + timedelta(days=PERIOD_DAYS[subscription.period])

    if invoice is None:
        invoice = Invoice(
            subscription=subscription, provider_payment_id=provider_payment_id
        )
    invoice.amount = amount
    invoice.period = subscription.period
    invoice.period_start = starts_at
    invoice.period_end = period_end
    invoice.status = Invoice.Status.PAID
    invoice.paid_at = now
    invoice.receipt = receipt
    invoice.save()

    was_suspended = subscription.status == Subscription.Status.SUSPENDED
    subscription.status = Subscription.Status.ACTIVE
    subscription.current_period_end = period_end
    subscription.grace_ends_at = None
    subscription.save(update_fields=["status", "current_period_end", "grace_ends_at"])

    if was_suspended:
        # Spec: to'lovdan keyin menyu DARHOL tiklanadi — 60s keshni kutmaydi.
        bump_menu_version(subscription.restaurant.slug)

    return invoice


@transaction.atomic
def approve_receipt(receipt: PaymentReceipt) -> Invoice | None:
    """Chekni tasdiqlaydi va obunani uzaytiradi.

    Allaqachon ko'rib chiqilgan chek uchun `None` qaytaradi — tugma ikki
    marta bosilsa ikkinchi hisob-faktura yozilmaydi.
    """
    fresh = PaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
    if fresh.status != PaymentReceipt.Status.PENDING:
        return None

    subscription = fresh.subscription
    # To'langan davr tanlanganidan farq qilishi mumkin — chekdagisiga moslaymiz.
    if subscription.period != fresh.period:
        subscription.period = fresh.period
        subscription.save(update_fields=["period"])

    fresh.status = PaymentReceipt.Status.APPROVED
    fresh.reviewed_at = timezone.now()
    fresh.save(update_fields=["status", "reviewed_at"])

    return record_payment(
        subscription,
        provider_payment_id=f"receipt_{fresh.pk}",
        amount=fresh.amount,
        receipt=fresh,
    )


@transaction.atomic
def reject_receipt(receipt: PaymentReceipt, note: str = "") -> bool:
    """Chekni rad etadi. Obunaga tegilmaydi. Qayta rad etilsa `False`."""
    fresh = PaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
    if fresh.status != PaymentReceipt.Status.PENDING:
        return False

    fresh.status = PaymentReceipt.Status.REJECTED
    fresh.note = note
    fresh.reviewed_at = timezone.now()
    fresh.save(update_fields=["status", "note", "reviewed_at"])
    return True
