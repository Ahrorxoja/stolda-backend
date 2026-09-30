"""Restoran egalariga Telegram xabarlari (@Stoldabot).

Faqat Telegram'ini ulagan egalarga boradi (`Profile.telegram_id`). Ulanmagan
bo'lsa — jim o'tadi: xabar qo'shimcha qulaylik, asosiy oqim undan qolmaydi.
Tranzaksiyadan keyin yuboriladi, Telegram xatosi so'rovni buzmaydi.
"""

import logging

from django.conf import settings
from django.db import transaction

from telegrambot import TelegramError, get_restaurant_bot

from .models import Profile, Restaurant, RestaurantMember

logger = logging.getLogger(__name__)


def owner_chats(restaurant: Restaurant, *, changes_only: bool = False) -> list[str]:
    """Egalarning Telegram chatlari. `changes_only` — "xodim o'zgartirdi" xabarini o'chirmaganlar."""
    members = RestaurantMember.objects.filter(restaurant=restaurant, role=RestaurantMember.Role.OWNER)
    if changes_only:
        members = members.filter(notify_changes=True)
    return list(
        Profile.objects.filter(user__in=members.values("user"), telegram_id__isnull=False).values_list(
            "telegram_id", flat=True
        )
    )


def notify_owners(
    restaurant: Restaurant,
    text: str,
    *,
    buttons: list[tuple[str, str]] | None = None,
    changes_only: bool = False,
) -> None:
    chats = owner_chats(restaurant, changes_only=changes_only)
    if not chats:
        return

    def send():
        bot = get_restaurant_bot()
        for chat_id in chats:
            try:
                if buttons:
                    bot.send_inline(chat_id, text, buttons)
                else:
                    bot.send(chat_id, text)
            except TelegramError as error:
                logger.warning("Restoranga xabar yuborilmadi (%s): %s", restaurant.slug, error)

    transaction.on_commit(send)


def app_url() -> str:
    """Restoranlar Mini App'i — Telegram faqat HTTPS'ni ochadi (localda bo'sh)."""
    return f"{settings.SITE_URL}/app" if settings.SITE_URL.startswith("https://") else ""


# ── Obuna va to'lov ────────────────────────────────────────────────────

SUBSCRIPTION_TEXTS = {
    "trial_soon": "⏰ <b>{name}</b>: bepul sinov {date} da tugaydi. Menyu to'xtamasligi uchun to'lovni qiling — "
    "ilovadagi «To'lov» bo'limida yoki admin panelda.",
    "due_soon": "⏰ <b>{name}</b>: to'lov muddati {date} da tugaydi. Oldindan to'lab qo'ying.",
    "past_due": "💳 <b>{name}</b>: to'lov muddati o'tdi. 7 kun ichida to'lasangiz, menyu to'xtamaydi.",
    "suspended": "⛔️ <b>{name}</b>: to'lanmagani uchun menyu to'xtatildi. To'lov qilinishi bilan darhol tiklanadi.",
}


def subscription_event(restaurant: Restaurant, kind: str, date: str = "") -> None:
    template = SUBSCRIPTION_TEXTS.get(kind)
    if template:
        notify_owners(restaurant, template.format(name=restaurant.name, date=date))


def receipt_reviewed(restaurant: Restaurant, *, approved: bool, until: str = "", note: str = "") -> None:
    if approved:
        text = f"✅ <b>{restaurant.name}</b>: to'lov tasdiqlandi." + (f" Menyu {until} gacha ishlaydi." if until else "")
    else:
        text = f"❌ <b>{restaurant.name}</b>: chek rad etildi." + (f"\nSabab: {note}" if note else "") + (
            "\nSavollar bo'lsa: @aha_daragoy"
        )
    notify_owners(restaurant, text)
