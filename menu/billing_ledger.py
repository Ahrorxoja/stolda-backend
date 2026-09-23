"""To'lov muvaffaqiyatli bo'lganda bazani yangilaydigan yagona joy.

Payme va Click webhook'lari turli shaklda keladi (JSON-RPC va forma
POST), lekin ikkalasi ham shu funksiyani chaqiradi — shuning uchun bir xil
to'lov ikki marta webhook bilan kelsa ham obuna faqat bir marta
uzaytiriladi (`Invoice.provider_payment_id` unique => `get_or_create`).
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .cache import bump_menu_version
from .models import Invoice, Subscription

PERIOD_DAYS = {Subscription.Period.MONTH: 30, Subscription.Period.YEAR: 365}


@transaction.atomic
def create_pending_invoice(
    subscription: Subscription, *, provider_payment_id: str, amount: int
) -> Invoice:
    """Payme kabi ikki bosqichli oqim uchun — `CreateTransaction`da chaqiriladi.

    `record_payment` shu qatorni keyin "paid"ga o'tkazadi. Bir martalik
    (Click, `FakeProvider`) oqimlar buni chaqirmasdan to'g'ridan-to'g'ri
    `record_payment`ga o'tadi.
    """
    invoice, _ = Invoice.objects.get_or_create(
        provider_payment_id=provider_payment_id,
        defaults={
            "subscription": subscription,
            "amount": amount,
            "period": subscription.period,
            "period_start": timezone.now(),
            "period_end": timezone.now(),
            "status": Invoice.Status.PENDING,
        },
    )
    return invoice


@transaction.atomic
def record_payment(
    subscription: Subscription,
    *,
    provider_payment_id: str,
    amount: int,
    receipt_url: str = "",
) -> Invoice:
    """To'lovni "to'landi" deb belgilaydi va obunani darhol tiklaydi/uzaytiradi.

    Idempotent — ikkinchi chaqiruv (bir xil `provider_payment_id` bilan
    takror kelgan webhook) hech narsani qayta o'zgartirmaydi. Ilgari
    `create_pending_invoice` bilan yaratilgan qator bo'lsa, o'shani "paid"ga
    o'tkazadi; bo'lmasa, yangisini yaratadi (Click/soxta provayder oqimi).
    """
    invoice = Invoice.objects.select_for_update().filter(
        provider_payment_id=provider_payment_id
    ).first()
    if invoice is not None and invoice.status == Invoice.Status.PAID:
        return invoice

    now = timezone.now()
    period_end = now + timedelta(days=PERIOD_DAYS[subscription.period])

    if invoice is None:
        invoice = Invoice(subscription=subscription, provider_payment_id=provider_payment_id)
    invoice.amount = amount
    invoice.period = subscription.period
    invoice.period_start = now
    invoice.period_end = period_end
    invoice.status = Invoice.Status.PAID
    invoice.paid_at = now
    if receipt_url:
        invoice.receipt_url = receipt_url
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
