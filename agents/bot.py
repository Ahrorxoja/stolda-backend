"""Agentlar boti: havola va QR, balans, restoranlar, karta, pul yechish.

Har bir kelgan xabar `handle_message` ga beriladi — Telegram'ning o'ziga
bog'liq emas, shuning uchun testda `FakeChatBot` bilan to'liq tekshiriladi.

Agent botga faqat shaxsiy taklif havolasi orqali ulanadi
(`t.me/<bot>?start=<invite_token>`) — kodni bilgan begona odam
boshqaning balansini ko'ra olmaydi.
"""

import io
import logging
import re

from django.db import transaction

from menu import qr as qr_codes

from . import services
from .models import Agent, AgentEarning

logger = logging.getLogger(__name__)

BTN_LINK = "🔗 Havolam"
BTN_BALANCE = "💰 Balans"
BTN_RESTAURANTS = "🍽 Restoranlarim"
BTN_WITHDRAW = "💸 Pul yechish"
BTN_CARD = "💳 Karta"
BTN_HELP = "❓ Yordam"

KEYBOARD = [[BTN_LINK, BTN_BALANCE], [BTN_RESTAURANTS, BTN_WITHDRAW], [BTN_CARD, BTN_HELP]]

STATE_CARD_NUMBER = "card_number"
STATE_CARD_HOLDER = "card_holder"

SUPPORT = "@aha_daragoy"

STATUS_LABELS = {
    "trialing": "🟡 sinovda",
    "active": "🟢 to'layapti",
    "past_due": "🟠 to'lov kutilmoqda",
    "suspended": "🔴 to'xtatilgan",
    "canceled": "⚪ bekor qilingan",
}


def handle_message(bot, message: dict) -> None:
    chat = message.get("chat") or {}
    if chat.get("type", "private") != "private":
        return  # Guruhlarda ishlamaydi — balans shaxsiy ma'lumot.
    chat_id = str(chat.get("id", ""))
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if text.startswith("/start"):
        token = text.split(maxsplit=1)[1].strip() if " " in text else ""
        _start(bot, chat_id, token, message.get("from") or {})
        return

    agent = Agent.objects.filter(telegram_chat_id=chat_id).first()
    if agent is None:
        bot.send(
            chat_id,
            "Bu bot stolda.uz agentlari uchun. Agent bo'lish uchun bizga yozing: " + SUPPORT,
        )
        return
    # Karta kiritish bosqichi — tugma bosilsa bosqich bekor bo'ladi.
    if agent.bot_state and text not in _BUTTONS:
        _continue_card(bot, agent, text)
        return
    if agent.bot_state:
        _set_state(agent, "")

    handler = _BUTTONS.get(text) or _COMMANDS.get(text.split("@")[0].lower())
    if handler is None:
        bot.send(chat_id, "Pastdagi tugmalardan birini tanlang 👇", keyboard=KEYBOARD)
        return
    # Faolligi o'chirilgan agent yangi restoran olib kela olmaydi, lekin
    # topgan pulini ko'rishi va yechib olishi mumkin.
    if not agent.is_active and handler not in _ALLOWED_WHEN_INACTIVE:
        bot.send(
            chat_id,
            "Agent hisobingiz faol emas — yangi restoran ulab bo'lmaydi. "
            "Balansni ko'rish va yechish mumkin. Savollar: " + SUPPORT,
            keyboard=KEYBOARD,
        )
        return
    handler(bot, agent)


# ── /start ─────────────────────────────────────────────────────────────


def _start(bot, chat_id: str, token: str, sender: dict) -> None:
    agent = Agent.objects.filter(telegram_chat_id=chat_id).first()
    if agent is None and token:
        candidate = Agent.objects.filter(invite_token=token, is_active=True).first()
        if candidate and candidate.telegram_chat_id and candidate.telegram_chat_id != chat_id:
            bot.send(chat_id, "Bu taklif havolasi boshqa Telegram hisobiga ulangan. " + SUPPORT)
            return
        if candidate:
            candidate.telegram_chat_id = chat_id
            candidate.telegram_username = (sender.get("username") or "")[:64]
            candidate.save(update_fields=["telegram_chat_id", "telegram_username"])
            agent = candidate

    if agent is None:
        bot.send(
            chat_id,
            "Assalomu alaykum! Bu bot stolda.uz agentlari uchun.\n"
            "Agent bo'lish uchun bizga yozing: " + SUPPORT,
        )
        return

    bot.send(
        chat_id,
        f"Assalomu alaykum, <b>{agent.name}</b>! 👋\n\n"
        f"Siz stolda.uz agentisiz. Kodingiz: <b>{agent.code}</b>\n\n"
        f"Restoran sizning havolangiz yoki kodingiz bilan ro'yxatdan o'tsa, "
        f"uning har to'lovidan <b>{agent.percent}%</b> ({agent.months} oy davomida) "
        f"va birinchi to'lov uchun <b>{services.money(agent.first_bonus)}</b> bonus olasiz.\n\n"
        f"Boshlash uchun «{BTN_LINK}» ni bosing.",
        keyboard=KEYBOARD,
    )


# ── Tugmalar ───────────────────────────────────────────────────────────


def _link(bot, agent: Agent) -> None:
    link = services.agent_link(agent)
    image = qr_codes.render(link, center="icon", box_size=16)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    bot.send_photo(
        agent.telegram_chat_id,
        buffer.getvalue(),
        caption=(
            f"🔗 Havolangiz: {link}\n"
            f"🔑 Kodingiz: <b>{agent.code}</b>\n\n"
            f"Restoran egasi shu QR'ni skanerlab ro'yxatdan o'tsa — kod o'zi yoziladi. "
            f"Havolasiz kelsa, ro'yxatdan o'tishda «Agent kodi» maydoniga {agent.code} ni yozadi."
        ),
    )


def _balance(bot, agent: Agent) -> None:
    b = services.balance(agent)
    lines = [
        "💰 <b>Balans</b>\n",
        f"Yechish mumkin: <b>{services.money(b.available)}</b>",
    ]
    if b.pending:
        lines.append(f"Ko'rib chiqilmoqda: {services.money(b.pending)}")
    lines += [
        f"Kartaga o'tkazilgan: {services.money(b.paid)}",
        f"Shu oyda topilgan: {services.money(b.this_month)}",
    ]
    if b.available < services.MIN_WITHDRAWAL:
        lines.append(f"\nYechish {services.money(services.MIN_WITHDRAWAL)} dan boshlanadi.")
    bot.send(agent.telegram_chat_id, "\n".join(lines), keyboard=KEYBOARD)


def _restaurants(bot, agent: Agent) -> None:
    restaurants = list(agent.restaurants.select_related("subscription").order_by("-agent_attached_at")[:30])
    if not restaurants:
        bot.send(
            agent.telegram_chat_id,
            "Hali restoran yo'q. «" + BTN_LINK + "» dagi QR'ni restoran egasiga ko'rsating 🙂",
            keyboard=KEYBOARD,
        )
        return

    earned: dict[int, int] = {}
    for row in AgentEarning.objects.filter(agent=agent).values("restaurant_id", "amount"):
        earned[row["restaurant_id"]] = earned.get(row["restaurant_id"], 0) + row["amount"]

    lines = [f"🍽 <b>Restoranlaringiz</b> ({agent.restaurants.count()})\n"]
    for restaurant in restaurants:
        subscription = getattr(restaurant, "subscription", None)
        status = STATUS_LABELS.get(subscription.status if subscription else "", "—")
        total = earned.get(restaurant.pk, 0)
        lines.append(f"• <b>{restaurant.name}</b> — {status}" + (f" · {services.money(total)}" if total else ""))
    bot.send(agent.telegram_chat_id, "\n".join(lines), keyboard=KEYBOARD)


def _withdraw(bot, agent: Agent) -> None:
    try:
        withdrawal = services.request_withdrawal(agent)
    except services.WithdrawalError as error:
        bot.send(agent.telegram_chat_id, f"⚠️ {error}", keyboard=KEYBOARD)
        return
    bot.send(
        agent.telegram_chat_id,
        f"✅ So'rov yuborildi: <b>{services.money(withdrawal.amount)}</b>\n"
        f"Karta: {services.mask_card(withdrawal.card_number)}\n\n"
        f"Pul o'tkazilgach shu yerga xabar keladi.",
        keyboard=KEYBOARD,
    )


def _card(bot, agent: Agent) -> None:
    current = (
        f"Hozirgi karta: {services.mask_card(agent.card_number)} ({agent.card_holder})\n\n"
        if agent.card_number
        else ""
    )
    _set_state(agent, STATE_CARD_NUMBER)
    bot.send(
        agent.telegram_chat_id,
        current + "💳 Karta raqamini yuboring (16 ta raqam), masalan: 8600 1234 5678 9012",
    )


def _help(bot, agent: Agent) -> None:
    bot.send(
        agent.telegram_chat_id,
        f"<b>Qanday ishlaydi</b>\n\n"
        f"1. «{BTN_LINK}» — havolangiz va QR. Restoran egasi shu orqali ro'yxatdan o'tadi.\n"
        f"2. Restoran 14–21 kunlik sinovdan keyin to'lasa — sizga daromad yoziladi.\n"
        f"3. Balans {services.money(services.MIN_WITHDRAWAL)} dan oshsa — «{BTN_WITHDRAW}».\n\n"
        f"Savollar: {SUPPORT}",
        keyboard=KEYBOARD,
    )


_BUTTONS = {
    BTN_LINK: _link,
    BTN_BALANCE: _balance,
    BTN_RESTAURANTS: _restaurants,
    BTN_WITHDRAW: _withdraw,
    BTN_CARD: _card,
    BTN_HELP: _help,
}
_ALLOWED_WHEN_INACTIVE = {_balance, _withdraw, _card, _help}

_COMMANDS = {
    "/havola": _link,
    "/balans": _balance,
    "/restoranlar": _restaurants,
    "/yechish": _withdraw,
    "/karta": _card,
    "/yordam": _help,
    "/help": _help,
}


# ── Karta kiritish ─────────────────────────────────────────────────────


def _set_state(agent: Agent, state: str) -> None:
    agent.bot_state = state
    agent.save(update_fields=["bot_state"])


CARD_DIGITS = re.compile(r"\D+")


def _continue_card(bot, agent: Agent, text: str) -> None:
    chat_id = agent.telegram_chat_id
    if agent.bot_state == STATE_CARD_NUMBER:
        digits = CARD_DIGITS.sub("", text)
        if len(digits) != 16:
            bot.send(chat_id, "Karta raqami 16 ta raqamdan iborat bo'lishi kerak. Qaytadan yuboring:")
            return
        with transaction.atomic():
            agent.card_number = " ".join(digits[i : i + 4] for i in range(0, 16, 4))
            agent.bot_state = STATE_CARD_HOLDER
            agent.save(update_fields=["card_number", "bot_state"])
        bot.send(chat_id, "Karta egasining ism-familiyasini yuboring (kartadagidek):")
        return

    if agent.bot_state == STATE_CARD_HOLDER:
        holder = " ".join(text.split()).upper()[:64]
        if len(holder) < 3:
            bot.send(chat_id, "Ism-familiyani to'liq yozing:")
            return
        agent.card_holder = holder
        agent.bot_state = ""
        agent.save(update_fields=["card_holder", "bot_state"])
        bot.send(
            chat_id,
            f"✅ Karta saqlandi: {services.mask_card(agent.card_number)} ({holder})",
            keyboard=KEYBOARD,
        )
        return

    _set_state(agent, "")
