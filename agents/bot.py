"""Agentlar boti: havola va QR, balans, restoranlar, karta, pul yechish.

Har bir kelgan xabar `handle_message` ga beriladi — Telegram'ning o'ziga
bog'liq emas, shuning uchun testda `FakeChatBot` bilan to'liq tekshiriladi.

Agent bo'lishning ikki yo'li:
- **Ariza** — istalgan odam botda "Agent bo'lish" ni bosib ism, telefon
  (Telegram tasdiqlagan), shahar va tanishlari haqida yozadi; platforma
  egasi cheklar botida "Qabul / Rad" ni bosadi, qabul qilinsa kod beriladi.
- **Taklif havolasi** — Django admin'da qo'lda qo'shilgan agent uchun
  (`t.me/<bot>?start=<invite_token>`), bitta Telegram hisobiga bog'lanadi.

Balans va boshqa ma'lumot faqat o'sha agentning chatida ko'rinadi.
"""

import io
import logging
import re

from django.db import transaction

from menu import qr as qr_codes
from menu.phones import is_valid_phone, normalize_phone

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

BTN_APPLY = "📝 Agent bo'lish"
BTN_SKIP = "O'tkazib yuborish"
BTN_CONTACT = {"text": "📱 Raqamni yuborish", "request_contact": True}
CITY_KEYBOARD = [["Toshkent", "Samarqand"], ["Buxoro", "Farg'ona"], ["Andijon", "Namangan"]]

STATE_APPLY_NAME = "apply_name"
STATE_APPLY_PHONE = "apply_phone"
STATE_APPLY_CITY = "apply_city"
STATE_APPLY_NOTE = "apply_note"
APPLY_STATES = {STATE_APPLY_NAME, STATE_APPLY_PHONE, STATE_APPLY_CITY, STATE_APPLY_NOTE}

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
    contact = message.get("contact")
    sender = message.get("from") or {}
    if not chat_id or not (text or contact):
        return

    if text.startswith("/start"):
        token = text.split(maxsplit=1)[1].strip() if " " in text else ""
        _start(bot, chat_id, token, sender)
        return

    agent = Agent.objects.filter(telegram_chat_id=chat_id).first()
    if agent is None:
        if text == BTN_APPLY:
            _begin_application(bot, chat_id, sender)
        else:
            _guest_welcome(bot, chat_id)
        return
    if agent.bot_state in APPLY_STATES:
        _continue_application(bot, agent, text, contact, sender)
        return
    if agent.rejected_at:
        bot.send(chat_id, _REJECTED, keyboard=[])
        return
    if agent.is_pending:
        bot.send(chat_id, _PENDING, keyboard=[])
        return
    if not text:
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
        _guest_welcome(bot, chat_id)
        return
    if agent.bot_state in APPLY_STATES:
        _ask_next(bot, agent)
        return
    if agent.rejected_at:
        bot.send(chat_id, _REJECTED, keyboard=[])
        return
    if agent.is_pending:
        bot.send(chat_id, _PENDING, keyboard=[])
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


# ── Ariza ──────────────────────────────────────────────────────────────

_PENDING = "⏳ Arizangiz ko'rib chiqilmoqda. Javob shu yerga keladi."
_REJECTED = "Arizangiz ko'rib chiqilgan, afsuski hozircha qabul qila olmaymiz. Savollar: " + SUPPORT


def _guest_welcome(bot, chat_id: str) -> None:
    field = Agent._meta.get_field
    bot.send(
        chat_id,
        "Assalomu alaykum! 👋\n\n"
        "<b>stolda.uz</b> — restoran va kafelar uchun QR menyu. Agent sifatida restoranlarni "
        "ulaysiz va ularning har to'lovidan daromad olasiz:\n\n"
        f"• har to'lovdan <b>{field('percent').default}%</b> — {field('months').default} oy davomida\n"
        f"• birinchi to'lov uchun <b>{services.money(field('first_bonus').default)}</b> bonus\n"
        "• pulni istalgan vaqtda kartaga yechib olasiz\n\n"
        f"Agent bo'lish uchun «{BTN_APPLY}» ni bosing — 1 daqiqa.",
        keyboard=[[BTN_APPLY]],
    )


def _begin_application(bot, chat_id: str, sender: dict) -> None:
    agent = Agent.objects.create(
        name="",
        is_active=False,
        telegram_chat_id=chat_id,
        telegram_username=(sender.get("username") or "")[:64],
        bot_state=STATE_APPLY_NAME,
    )
    _ask_next(bot, agent)


def _ask_next(bot, agent: Agent) -> None:
    """Joriy bosqichning savoli (bot qayta ochilsa ham shu yerdan davom etadi)."""
    chat_id = agent.telegram_chat_id
    if agent.bot_state == STATE_APPLY_NAME:
        bot.send(chat_id, "1/4. Ism-familiyangizni yozing:", keyboard=[])
    elif agent.bot_state == STATE_APPLY_PHONE:
        bot.send(
            chat_id,
            "2/4. Telefon raqamingizni yuboring — pastdagi tugmani bosing 👇",
            keyboard=[[BTN_CONTACT]],
        )
    elif agent.bot_state == STATE_APPLY_CITY:
        bot.send(chat_id, "3/4. Qaysi shahardasiz? Tanlang yoki yozing:", keyboard=CITY_KEYBOARD)
    elif agent.bot_state == STATE_APPLY_NOTE:
        bot.send(
            chat_id,
            "4/4. Restoran yoki kafe egalari bilan tanishlaringiz bormi? Qisqacha yozing "
            "(masalan: «5–6 ta tanish restoran bor»).",
            keyboard=[[BTN_SKIP]],
        )


def _continue_application(bot, agent: Agent, text: str, contact: dict | None, sender: dict) -> None:
    chat_id = agent.telegram_chat_id
    state = agent.bot_state

    if state == STATE_APPLY_NAME:
        name = " ".join(text.split())[:80]
        if len(name) < 3 or name.startswith("/"):
            bot.send(chat_id, "Ism-familiyani to'liq yozing:")
            return
        agent.name, agent.bot_state = name, STATE_APPLY_PHONE

    elif state == STATE_APPLY_PHONE:
        if contact:
            # Faqat o'z raqami — boshqaning kontaktini yuborib bo'lmasin.
            if contact.get("user_id") and sender.get("id") and contact["user_id"] != sender["id"]:
                bot.send(chat_id, "Iltimos, o'zingizning raqamingizni yuboring 👇", keyboard=[[BTN_CONTACT]])
                return
            phone = normalize_phone(contact.get("phone_number", ""))
        elif is_valid_phone(text):
            phone = normalize_phone(text)
        else:
            bot.send(chat_id, "Pastdagi «📱 Raqamni yuborish» tugmasini bosing 👇", keyboard=[[BTN_CONTACT]])
            return
        agent.phone, agent.bot_state = phone[:20], STATE_APPLY_CITY

    elif state == STATE_APPLY_CITY:
        city = " ".join(text.split())[:60]
        if len(city) < 2:
            bot.send(chat_id, "Shahar nomini yozing:", keyboard=CITY_KEYBOARD)
            return
        agent.city, agent.bot_state = city, STATE_APPLY_NOTE

    elif state == STATE_APPLY_NOTE:
        agent.note = "" if text == BTN_SKIP else " ".join(text.split())[:300]
        agent.bot_state = ""
        agent.save(update_fields=["name", "phone", "city", "note", "bot_state"])
        services.submit_application(agent)
        bot.send(
            chat_id,
            "✅ Arizangiz yuborildi! Tez orada ko'rib chiqamiz — javob shu yerga keladi.",
            keyboard=[],
        )
        return

    agent.save(update_fields=["name", "phone", "city", "note", "bot_state"])
    _ask_next(bot, agent)


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
