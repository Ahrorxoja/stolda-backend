"""@Stoldabot — restoran egalari, xodimlar va platforma egasi uchun.

Bitta bot, har kimga o'z roli:

* **Restoran egasi va xodimlar** — menyu tugmasidagi Mini App (`/app`),
  to'lov eslatmalari, xodim o'zgarishlari haqida xabar ("↩️ Qaytarish" bilan).
* **Platforma egasi** (`TELEGRAM_ADMIN_CHAT_ID`) — yuqoridagilar ustiga cheklar,
  agent arizalari, pul so'rovlari (`telegram_bot` buyrug'i) va `/platforma`.

Polling `manage.py telegram_bot` da; bu modul faqat xabarlarga javob beradi.
"""

import logging

from django.conf import settings
from django.utils import timezone

from telegrambot import TelegramError

from . import activity, platform
from .models import ActivityLog, Profile, RestaurantMember
from .notify import app_url

logger = logging.getLogger(__name__)

COMMANDS = [
    ("start", "Boshlash"),
    ("ilova", "Restoran ilovasini ochish"),
    ("menyu", "Menyu havolasi"),
    ("yordam", "Yordam"),
]
#: Faqat platforma egasining chatida ko'rinadi (Telegram "scope" bilan).
ADMIN_COMMANDS = COMMANDS + [("platforma", "Platforma: shu oy raqamlari")]
MENU_APP_LABEL = "Ilova"
SUPPORT = "@aha_daragoy"


def setup(bot) -> None:
    """Bot ishga tushganda: buyruqlar ro'yxati va menyu tugmasi (Mini App)."""
    try:
        bot.set_commands(COMMANDS)
        if settings.TELEGRAM_ADMIN_CHAT_ID:
            bot.set_commands(ADMIN_COMMANDS, chat_id=str(settings.TELEGRAM_ADMIN_CHAT_ID))
        if app_url():
            bot.set_menu_app(MENU_APP_LABEL, app_url())
    except TelegramError as error:
        logger.warning("Buyruqlar yoki menyu tugmasi o'rnatilmadi: %s", error)


def _is_admin(chat_id: str) -> bool:
    return bool(settings.TELEGRAM_ADMIN_CHAT_ID) and chat_id == str(settings.TELEGRAM_ADMIN_CHAT_ID)


def _open_app(bot, chat_id: str, text: str, payload: str = "") -> None:
    url = app_url()
    if not url:
        bot.send(chat_id, text + "\n\n(Ilova faqat stolda.uz saytida ishlaydi.)")
        return
    bot.send_web_app(chat_id, text, "📱 Ilovani ochish", f"{url}?p={payload}" if payload else url)


def _membership(chat_id: str) -> RestaurantMember | None:
    profile = Profile.objects.filter(telegram_id=chat_id).select_related("user").first()
    if profile is None:
        return None
    return RestaurantMember.objects.select_related("restaurant").filter(user=profile.user).first()


UNLINKED = (
    "Salom! Bu <b>stolda.uz</b> — restoran va kafelar uchun QR menyu boti.\n\n"
    "• <b>Restoraningiz bormi?</b> Admin panelda «Sozlamalar» → «Telegram'ni ulash» ni bosing.\n"
    "• <b>Xodimmisiz?</b> Restoran egasidan taklif havolasini so'rang.\n"
    "• <b>Yangi restoran:</b> stolda.uz/signup\n\n"
    f"Savollar: {SUPPORT}"
)


def handle_message(bot, message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (message.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        if chat_id and chat.get("type", "private") == "private" and text:
            bot.send(chat_id, "Pastdagi «Ilova» tugmasini bosing yoki /yordam 🙂")
        return
    command, _, argument = text.partition(" ")
    command = command.split("@")[0].lower()
    argument = argument.strip()

    if command == "/platforma":
        if _is_admin(chat_id):
            now = timezone.localtime()
            bot.send(chat_id, platform.monthly_report_text(now.year, now.month).replace("oylik hisobot", "shu oy"))
        return
    if chat.get("type", "private") != "private":
        return  # Guruhda faqat platforma egasining buyrug'i.

    if command == "/start" and argument[:1] in ("L", "I"):
        invite = argument.startswith("I")
        _open_app(
            bot,
            chat_id,
            "👋 Restoran jamoasiga qo'shilish uchun pastdagi tugmani bosing."
            if invite
            else "🔗 Telegram'ni stolda.uz hisobingizga ulash uchun pastdagi tugmani bosing.",
            argument[:80],
        )
        return

    member = _membership(chat_id)
    if command in ("/start", "/ilova"):
        if member is None:
            bot.send(chat_id, UNLINKED)
            return
        role = "egasi" if member.is_owner else "jamoasi"
        _open_app(
            bot,
            chat_id,
            f"👋 <b>{member.restaurant.name}</b> {role} sifatida ulangansiz.\n\n"
            "Stop-list, narxlar, taom qo'shish va statistika — ilovada. "
            "Xabarlar (to'lov, o'zgarishlar) shu chatga keladi.",
        )
        return
    if command == "/menyu":
        if member is None:
            bot.send(chat_id, UNLINKED)
            return
        bot.send(chat_id, f"🍽 <b>{member.restaurant.name}</b> menyusi:\n{settings.SITE_URL}/{member.restaurant.slug}")
        return
    if command in ("/yordam", "/help"):
        bot.send(
            chat_id,
            "<b>stolda.uz boti</b>\n\n"
            "• «Ilova» tugmasi — stop-list, narx, taom qo'shish, statistika, to'lov.\n"
            "• /menyu — menyungiz havolasi.\n"
            "• To'lov muddati, chek tasdiqlanishi va xodimlar o'zgarishlari haqida xabar shu yerga keladi.\n\n"
            f"Savollar: {SUPPORT}",
        )
        return
    bot.send(chat_id, "Bunday buyruq yo'q. /yordam")


def handle_undo(bot, callback: dict) -> None:
    """"↩️ Qaytarish" — xabar kelgan egasi bosadi (Telegram ID si bo'yicha tekshiriladi)."""
    sender = str((callback.get("from") or {}).get("id", ""))
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    raw_id = str(callback.get("data", "")).partition(":")[2]
    entry = ActivityLog.objects.select_related("restaurant").filter(pk=raw_id if raw_id.isdigit() else 0).first()
    profile = Profile.objects.filter(telegram_id=sender).select_related("user").first()
    if entry is None or profile is None:
        answer = "Topilmadi."
    else:
        try:
            answer = activity.undo(entry, profile.user)
        except activity.UndoError as error:
            answer = str(error)
    try:
        bot.answer_callback(str(callback.get("id", "")), answer[:190])
        if entry is not None and entry.pk and answer.startswith("Qaytarildi") and chat_id:
            bot.edit_chat_text(chat_id, str(message.get("message_id", "")), f"{message.get('text', '')}\n\n↩️ <b>{answer}</b>")
    except TelegramError as error:
        logger.warning("Qaytarish javobi yuborilmadi: %s", error)
