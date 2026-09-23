"""Fon vazifalari: AI tarjima, obuna hayot sikli."""

import logging
from datetime import timedelta

from celery import shared_task
from django.apps import apps
from django.utils import timezone

from billing import PaymentError, get_provider
from translation import TranslationError, get_translator

from . import translation_sync as sync
from .billing_ledger import record_payment
from .cache import bump_menu_version
from .models import Subscription

logger = logging.getLogger(__name__)

#: Tarjima qilinadigan modellar — task faqat shularni qabul qiladi.
SUPPORTED = {"Dish", "Category", "Restaurant"}


@shared_task
def retranslate_restaurant(restaurant_id: int) -> dict:
    """Restoranning hamma kategoriya va taomlarini qayta tarjima qiladi.

    Yangi til qo'shilganda chaqiriladi — o'sha til uchun matn hali yo'q.
    """
    Restaurant = apps.get_model("menu", "Restaurant")
    restaurant = Restaurant.objects.filter(pk=restaurant_id).first()
    if restaurant is None or not restaurant.auto_translate:
        return {"skipped": "topilmadi yoki avtomatik tarjima o'chirilgan"}

    Category = apps.get_model("menu", "Category")
    Dish = apps.get_model("menu", "Dish")

    queued = 0
    for category in Category.objects.filter(restaurant=restaurant):
        retranslate.delay("Category", category.pk)
        queued += 1
    for dish in Dish.objects.filter(category__restaurant=restaurant):
        retranslate.delay("Dish", dish.pk)
        queued += 1
    retranslate.delay("Restaurant", restaurant.pk)

    return {"queued": queued + 1}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def retranslate(self, model_name: str, pk: int) -> dict:
    """Obyektning asosiy tildagi matnini qolgan tillarga tarjima qiladi.

    `manual` deb belgilangan tillarga tegilmaydi (`translation_sync`).
    """
    if model_name not in SUPPORTED:
        raise ValueError(f"Bu model tarjima qilinmaydi: {model_name}")

    model = apps.get_model("menu", model_name)
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        return {"skipped": "topilmadi"}

    restaurant = sync.restaurant_of(obj)
    if restaurant is None or not restaurant.auto_translate:
        return {"skipped": "avtomatik tarjima o'chirilgan"}

    targets = sync.pending_languages(obj, restaurant)
    if not targets:
        return {"skipped": "o'zgarish yo'q"}

    texts = sync.source_texts(obj, restaurant.primary_language)
    try:
        translated = get_translator().translate(
            texts, source=restaurant.primary_language, targets=targets
        )
    except TranslationError as error:
        logger.warning("Tarjima bajarilmadi (%s %s): %s", model_name, pk, error)
        raise self.retry(exc=error)

    if not translated:
        return {"skipped": "provayder natija bermadi"}

    sync.apply_translations(obj, translated, restaurant.primary_language)
    fields = [field.name for field in sync.fields_for(obj)] + ["translation_meta"]
    try:
        obj.save(update_fields=fields)
    except ValueError:
        # Model tarjima uchun to'liq sozlanmagan bo'lsa (masalan `translation_meta`
        # yo'q) — jim qolmasin, log'da ko'rinsin.
        logger.exception("Tarjimani saqlab bo'lmadi: %s %s", model_name, pk)
        raise

    return {"translated": sorted(translated)}


def _attempt_charge(subscription: Subscription) -> bool:
    """Standart to'lov usuli orqali yechishga urinadi. Muvaffaqiyatli bo'lsa obunani yangilaydi."""
    if subscription.canceled_at is not None or not subscription.autopay:
        return False

    payment_method = subscription.restaurant.payment_methods.filter(is_default=True).first()
    if payment_method is None:
        return False

    amount = (
        subscription.plan.price_year
        if subscription.period == Subscription.Period.YEAR
        else subscription.plan.price_month
    )
    try:
        result = get_provider(payment_method.provider).charge_token(
            token=payment_method.token,
            amount=amount,
            description=f"stolda.uz — {subscription.plan.name}",
        )
    except PaymentError as error:
        logger.warning("To'lov muvaffaqiyatsiz (%s): %s", subscription.restaurant.slug, error)
        return False

    record_payment(
        subscription,
        provider_payment_id=result.provider_payment_id,
        amount=amount,
        receipt_url=result.receipt_url,
    )
    return True


@shared_task
def process_subscriptions() -> dict:
    """Har kuni bir marta: to'lov eslatmasi, avtomatik yechish, imtiyozli
    muddat va to'xtatish. Celery Beat orqali chaqiriladi (`config/settings.py`).
    """
    now = timezone.now()
    today = now.date()
    reminder_date = today + timedelta(days=3)
    stats = {"reminded": 0, "renewed": 0, "past_due": 0, "suspended": 0}

    active_like = Subscription.objects.select_related("restaurant", "plan").filter(
        status__in=[
            Subscription.Status.TRIALING,
            Subscription.Status.ACTIVE,
            Subscription.Status.PAST_DUE,
        ]
    )

    for subscription in active_like:
        if subscription.status == Subscription.Status.PAST_DUE:
            if subscription.grace_ends_at is None:
                continue
            grace_started = subscription.grace_ends_at - timedelta(days=7)
            days_since = (today - grace_started.date()).days
            reached_end = now >= subscription.grace_ends_at
            if days_since not in (1, 3, 5, 7) and not reached_end:
                continue
            if _attempt_charge(subscription):
                stats["renewed"] += 1
            elif reached_end:
                subscription.status = Subscription.Status.SUSPENDED
                subscription.save(update_fields=["status"])
                # Spec: "menyu darhol to'xtaydi" — 60s keshni kutmaydi.
                bump_menu_version(subscription.restaurant.slug)
                stats["suspended"] += 1
            continue

        # trialing / active — sinov yoki to'lov muddati.
        end_date = (
            subscription.trial_ends_at
            if subscription.status == Subscription.Status.TRIALING
            else subscription.current_period_end
        )
        if end_date is None:
            continue

        if end_date.date() == reminder_date:
            # TODO(7.6): Notification(kind="payment") — "3 kundan keyin yechiladi".
            logger.info("Eslatma: %s uchun to'lov 3 kundan keyin", subscription.restaurant.slug)
            stats["reminded"] += 1
        elif end_date <= now:
            if _attempt_charge(subscription):
                stats["renewed"] += 1
            else:
                subscription.status = Subscription.Status.PAST_DUE
                subscription.grace_ends_at = now + timedelta(days=7)
                subscription.save(update_fields=["status", "grace_ends_at"])
                stats["past_due"] += 1

    return stats
