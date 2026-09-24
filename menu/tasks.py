"""Fon vazifalari: AI tarjima, obuna hayot sikli."""

import logging
from datetime import timedelta

from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.utils import timezone

from telegrambot import TelegramError, get_bot
from translation import TranslationError, get_translator

from . import translation_sync as sync
from .billing_ledger import reject_receipt
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


#: Chekni yuborishga necha marta urinamiz. Telegram qisqa uzilib qolsa chek
#: yo'qolmasligi kerak — kutilayotgan chek yangisini yuklashni to'sib turadi.
RECEIPT_SEND_RETRIES = 5


@shared_task(bind=True, max_retries=RECEIPT_SEND_RETRIES)
def send_receipt_to_telegram(self, receipt_id: int) -> dict:
    """Yuklangan chekni platforma egasining Telegramiga yuboradi.

    Yuborish so'rov ichida emas, shu task'da — chek yuklash tashqi API'ni
    kutmaydi. Token sozlanmagan bo'lsa `FakeBot` ishlaydi va hech narsa
    yuborilmaydi (dev muhiti va testlar).

    Telegram javob bermasa 1, 2, 4… daqiqada qayta urinadi. Hamma urinish
    barbod bo'lsa chek `rejected` qilinadi — aks holda u abadiy
    "tekshirilmoqda" bo'lib qolar va egasi yangi chek ham yuklay olmasdi.
    """
    PaymentReceipt = apps.get_model("menu", "PaymentReceipt")
    receipt = (
        PaymentReceipt.objects.filter(pk=receipt_id)
        .select_related("subscription", "subscription__restaurant", "subscription__plan")
        .first()
    )
    if receipt is None:
        return {"skipped": "topilmadi"}

    restaurant = receipt.subscription.restaurant
    # Egasi bilan bog'lanish kerak bo'lsa — raqami va Telegrami shu yerda.
    profile = getattr(restaurant.owner, "profile", None)
    contact = (profile.contact_phone if profile else "") or restaurant.phone or "—"
    telegram = (profile.telegram if profile else "") or "—"
    caption = (
        f"<b>{restaurant.name}</b> ({settings.SITE_URL}/{restaurant.slug})\n"
        f"Summa: {receipt.amount:,} so'm\n".replace(",", " ")
        + f"Davr: {receipt.get_period_display()}\n"
        f"Egasi: {(profile.full_name if profile else '') or restaurant.owner.get_username()}\n"
        f"Telefon: {contact}\n"
        f"Telegram: {telegram}"
    )

    try:
        with receipt.image.open("rb") as image:
            message_id = get_bot().send_receipt(
                caption=caption,
                image=image,
                approve=f"approve:{receipt.pk}",
                reject=f"reject:{receipt.pk}",
            )
    except TelegramError as error:
        logger.warning("Chekni yuborib bo'lmadi (%s): %s", receipt.pk, error)
        if self.request.retries < RECEIPT_SEND_RETRIES:
            # Eksponensial kutish: 60, 120, 240, 480, 960 soniya.
            raise self.retry(countdown=60 * 2**self.request.retries, exc=error)

        reject_receipt(
            receipt,
            note="Chekni tekshiruvga yuborib bo'lmadi — iltimos qaytadan yuklang.",
        )
        return {"failed": str(error), "rejected": receipt.pk}

    if message_id:
        receipt.telegram_message_id = message_id
        receipt.save(update_fields=["telegram_message_id"])
    return {"sent": receipt.pk}


@shared_task
def process_subscriptions() -> dict:
    """Har kuni bir marta: to'lov eslatmasi, imtiyozli muddat va to'xtatish.

    To'lov qo'lda bo'lgani uchun bu yerda hech narsa yechilmaydi — muddat
    tugaganda obuna `past_due`ga o'tadi va egasi chek yuklaguncha shunday
    turadi. Celery Beat orqali chaqiriladi (`config/settings.py`).
    """
    now = timezone.now()
    today = now.date()
    reminder_date = today + timedelta(days=3)
    stats = {"reminded": 0, "past_due": 0, "suspended": 0}
    #: Kun oxirida platforma egasiga bitta umumiy xabar bo'lib boradi.
    digest: list[str] = []

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
            if now >= subscription.grace_ends_at:
                subscription.status = Subscription.Status.SUSPENDED
                subscription.save(update_fields=["status"])
                # Spec: "menyu darhol to'xtaydi" — 60s keshni kutmaydi.
                bump_menu_version(subscription.restaurant.slug)
                digest.append(f"⛔️ {_label(subscription)} — menyu to'xtatildi")
                stats["suspended"] += 1
            else:
                days = (subscription.grace_ends_at - now).days + 1
                logger.info(
                    "Eslatma: %s to'lamadi, imtiyozli muddat davom etmoqda",
                    subscription.restaurant.slug,
                )
                digest.append(
                    f"⚠️ {_label(subscription)} — to'lamadi, imtiyozli muddatga "
                    f"{days} kun qoldi"
                )
                stats["reminded"] += 1
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
            logger.info("Eslatma: %s uchun muddat 3 kundan keyin", subscription.restaurant.slug)
            digest.append(f"🔔 {_label(subscription)} — 3 kundan keyin tugaydi")
            stats["reminded"] += 1
        elif end_date <= now:
            subscription.status = Subscription.Status.PAST_DUE
            subscription.grace_ends_at = now + timedelta(days=7)
            subscription.save(update_fields=["status", "grace_ends_at"])
            digest.append(
                f"💳 {_label(subscription)} — muddat tugadi, 7 kun imtiyoz berildi"
            )
            stats["past_due"] += 1

    _send_digest(digest, today)
    return stats


def _label(subscription: Subscription) -> str:
    restaurant = subscription.restaurant
    phone = restaurant.phone or "telefon yo'q"
    return f"<b>{restaurant.name}</b> (/{restaurant.slug}, {phone})"


def _send_digest(lines: list[str], today) -> None:
    """Kunlik holat — platforma egasining Telegramiga.

    Restoran egasining o'zida hali bildirishnoma kanali yo'q (Telegram kirish
    qo'shilmagan), shuning uchun eslatma platforma egasiga boradi va u
    restoranga qo'ng'iroq qila oladi. Panelda ular `BillingBanner` ni ham
    ko'rib turadi.
    """
    if not lines:
        return
    text = f"<b>stolda.uz · {today:%d.%m.%Y}</b>\n\n" + "\n".join(lines)
    try:
        get_bot().send_message(text)
    except TelegramError as error:
        # Eslatma yetib bormasa ham obuna holati to'g'ri hisoblangan —
        # vazifani yiqitmaymiz.
        logger.warning("Kunlik eslatma yuborilmadi: %s", error)
