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
from django.utils import timezone

from menu import qr as qr_codes
from menu.regions import REGIONS, match_region

from . import journal, services
from .models import Agent, AgentEarning, Place, Visit
from .rules import RULES_VERSION, accepted_current, rules_parts

logger = logging.getLogger(__name__)

BTN_LINK = "🔗 Havolam"
BTN_BALANCE = "💰 Balans"
BTN_RESTAURANTS = "🍽 Restoranlarim"
BTN_WITHDRAW = "💸 Pul yechish"
BTN_CARD = "💳 Karta"
BTN_HELP = "❓ Yordam"
BTN_RULES = "📜 Qoidalar"

BTN_VISIT = "➕ Borgan joyim"
BTN_SEARCH = "🔍 Qidirish"
BTN_MY_VISITS = "📒 Borgan joylarim"
BTN_PLACES = "🗺 Barcha joylar"
BTN_PARTNERS = "🤝 Hamkorlarimiz"

KEYBOARD = [
    [BTN_VISIT, BTN_MY_VISITS],
    [BTN_SEARCH, BTN_PLACES],
    [BTN_PARTNERS, BTN_RESTAURANTS],
    [BTN_LINK, BTN_BALANCE, BTN_WITHDRAW],
    [BTN_CARD, BTN_RULES, BTN_HELP],
]

BTN_ALL = "📋 Hammasi"
BTN_ALL_REGIONS = "🌍 Hamma viloyatlar"
BTN_NEW_ADDRESS = "➕ Boshqa manzil (yangi joy)"

STATE_VISIT_REGION = "visit_region"
STATE_VISIT_NAME = "visit_name"
STATE_VISIT_PICK = "visit_pick"
STATE_VISIT_ADDRESS = "visit_address"
STATE_VISIT_OUTCOME = "visit_outcome"
STATE_VISIT_COMMENT = "visit_comment"
STATE_SEARCH_REGION = "search_region"
STATE_SEARCH_QUERY = "search_query"
STATE_PLACES_REGION = "places_region"
STATE_PARTNERS_REGION = "partners_region"
JOURNAL_STATES = {
    STATE_PLACES_REGION,
    STATE_PARTNERS_REGION,
    STATE_VISIT_REGION,
    STATE_VISIT_NAME,
    STATE_VISIT_PICK,
    STATE_VISIT_ADDRESS,
    STATE_VISIT_OUTCOME,
    STATE_VISIT_COMMENT,
    STATE_SEARCH_REGION,
    STATE_SEARCH_QUERY,
}

STATE_CARD_NUMBER = "card_number"
STATE_CARD_HOLDER = "card_holder"

BTN_APPLY = "📝 Agent bo'lish"
BTN_ACCEPT = "✅ Qoidalarga roziman"
BTN_CANCEL = "❌ Bekor qilish"
RULES_KEYBOARD = [[BTN_ACCEPT], [BTN_CANCEL]]
BTN_SKIP = "O'tkazib yuborish"
BTN_CONTACT = {"text": "📱 Raqamni yuborish", "request_contact": True}
REGION_KEYBOARD = [REGIONS[i : i + 2] for i in range(0, len(REGIONS), 2)]


UZ_PHONE = re.compile(r"^998[1-9]\d{8}$")


def _uz_phone(value: str) -> str:
    """To'liq O'zbekiston raqami → `+998901234567`, aks holda bo'sh.

    `90 123 45 67`, `+998 90 123-45-67`, `998901234567` — hammasi qabul.
    """
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 9:
        digits = "998" + digits
    return f"+{digits}" if UZ_PHONE.match(digits) else ""

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
        if text in (BTN_APPLY, "/qoidalar"):
            _show_rules(bot, chat_id, RULES_KEYBOARD)
        elif text == BTN_ACCEPT:
            # Qoidalarni ko'rib, rozi bo'ldi — ariza shundan boshlanadi.
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
    # Qo'lda qo'shilgan agent yoki qoidalar yangilangan — avval rozilik.
    if not accepted_current(agent):
        if text == BTN_ACCEPT:
            _accept_rules(agent)
            _welcome(bot, agent)
        else:
            _show_rules(bot, chat_id, RULES_KEYBOARD)
        return
    # Jurnal (borgan joy, qidiruv) — "Bekor qilish" yoki asosiy tugma chiqaradi.
    if agent.bot_state in JOURNAL_STATES:
        if text == BTN_CANCEL:
            _reset_journal(agent)
            bot.send(chat_id, "Bekor qilindi.", keyboard=KEYBOARD)
            return
        if text not in _BUTTONS:
            _continue_journal(bot, agent, text)
            return
        _reset_journal(agent)
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
    if not accepted_current(agent):
        _show_rules(bot, chat_id, RULES_KEYBOARD)
        return
    _welcome(bot, agent)


def _welcome(bot, agent: Agent) -> None:
    chat_id = agent.telegram_chat_id
    bot.send(
        chat_id,
        f"Assalomu alaykum, <b>{agent.name}</b>! 👋\n\n"
        f"Siz stolda.uz agentisiz. Kodingiz: <b>{agent.code}</b>\n\n"
        f"{services.terms(agent)}\n\n"
        f"Boshlash uchun «{BTN_LINK}» ni bosing.",
        keyboard=KEYBOARD,
    )


# ── Ariza ──────────────────────────────────────────────────────────────

_PENDING = "⏳ Arizangiz ko'rib chiqilmoqda. Javob shu yerga keladi."
_REJECTED = "Arizangiz ko'rib chiqilgan, afsuski hozircha qabul qila olmaymiz. Savollar: " + SUPPORT


def _show_rules(bot, chat_id: str, keyboard) -> None:
    """To'liq qoidalar — ikki xabar (Telegram chegarasi), tugmalar oxirgisida."""
    parts = rules_parts()
    for part in parts[:-1]:
        bot.send(chat_id, part)
    bot.send(chat_id, parts[-1], keyboard=keyboard)


def _accept_rules(agent: Agent) -> None:
    agent.rules_version = RULES_VERSION
    agent.rules_accepted_at = timezone.now()
    agent.save(update_fields=["rules_version", "rules_accepted_at"])


def _guest_welcome(bot, chat_id: str) -> None:
    field = Agent._meta.get_field
    bot.send(
        chat_id,
        "Assalomu alaykum! 👋\n\n"
        "<b>stolda.uz</b> — restoran va kafelar uchun QR menyu. Agent sifatida restoranlarni "
        "ulaysiz va ularning har to'lovidan daromad olasiz:\n\n"
        f"• restoranning <b>birinchi to'lovidan {field('first_percent').default}%</b> — oylik yoki yillik\n"
        f"• <b>keyingi to'lovlaridan {field('percent').default}%</b> — "
        f"{services.duration(field('months').default)}\n"
        "• pulni istalgan vaqtda kartaga yechib olasiz\n\n"
        f"Agent bo'lish uchun «{BTN_APPLY}» ni bosing: avval qoidalar bilan tanishasiz, "
        f"keyin qisqa ariza (1 daqiqa).",
        keyboard=[[BTN_APPLY]],
    )


def _begin_application(bot, chat_id: str, sender: dict) -> None:
    agent = Agent.objects.create(
        name="",
        is_active=False,
        telegram_chat_id=chat_id,
        telegram_username=(sender.get("username") or "")[:64],
        bot_state=STATE_APPLY_NAME,
        rules_version=RULES_VERSION,
        rules_accepted_at=timezone.now(),
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
            "2/4. Telefon raqamingizni yozing, masalan: <b>+998 90 123 45 67</b>\n"
            "yoki pastdagi «📱 Raqamni yuborish» tugmasini bosing 👇",
            keyboard=[[BTN_CONTACT]],
        )
    elif agent.bot_state == STATE_APPLY_CITY:
        bot.send(chat_id, "3/4. Qaysi viloyatda ishlaysiz? Pastdan tanlang 👇", keyboard=REGION_KEYBOARD)
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
            phone = _uz_phone(contact.get("phone_number", ""))
            if not phone:
                bot.send(
                    chat_id,
                    "Bu O'zbekiston raqami emas. O'zbekistondagi raqamingizni yozing, "
                    "masalan: +998 90 123 45 67",
                    keyboard=[[BTN_CONTACT]],
                )
                return
        else:
            phone = _uz_phone(text)
            if not phone:
                bot.send(
                    chat_id,
                    "❗️ Raqam noto'g'ri. To'liq yozing, masalan: <b>+998 90 123 45 67</b>\n"
                    "yoki pastdagi «📱 Raqamni yuborish» tugmasini bosing 👇",
                    keyboard=[[BTN_CONTACT]],
                )
                return
        agent.phone, agent.bot_state = phone[:20], STATE_APPLY_CITY

    elif state == STATE_APPLY_CITY:
        region = match_region(text)
        if not region:
            bot.send(chat_id, "Viloyatni pastdagi tugmalardan tanlang 👇", keyboard=REGION_KEYBOARD)
            return
        agent.city, agent.bot_state = region, STATE_APPLY_NOTE

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
        next_date = _next_date(subscription)
        if next_date:
            status += f" ({next_date})"
        total = earned.get(restaurant.pk, 0)
        lines.append(f"• <b>{restaurant.name}</b> — {status}" + (f" · {services.money(total)}" if total else ""))
    bot.send(agent.telegram_chat_id, "\n".join(lines), keyboard=KEYBOARD)


# ── Borilgan joylar jurnali ────────────────────────────────────────────

OUTCOME_KEYBOARD = [[label] for _, label in Visit.Outcome.choices] + [[BTN_CANCEL]]


def _region_keyboard():
    return REGION_KEYBOARD + [[BTN_CANCEL]]


def _reset_journal(agent: Agent) -> None:
    agent.bot_state, agent.bot_draft = "", {}
    agent.save(update_fields=["bot_state", "bot_draft"])


def _step(agent: Agent, state: str, **draft) -> None:
    agent.bot_state = state
    agent.bot_draft = {**(agent.bot_draft or {}), **draft}
    agent.save(update_fields=["bot_state", "bot_draft"])


def _visit(bot, agent: Agent) -> None:
    agent.bot_state, agent.bot_draft = STATE_VISIT_REGION, {}
    agent.save(update_fields=["bot_state", "bot_draft"])
    bot.send(agent.telegram_chat_id, "➕ <b>Borgan joyingiz</b>\n\n1/5. Qaysi viloyatda?", keyboard=_region_keyboard())


def _search(bot, agent: Agent) -> None:
    _step(agent, STATE_SEARCH_REGION)
    bot.send(agent.telegram_chat_id, "🔍 Qaysi viloyatda qidiramiz?", keyboard=_region_keyboard())


def _send_parts(bot, chat_id: str, parts: list[str]) -> None:
    for text in journal.chunks(parts):
        bot.send(chat_id, text, keyboard=KEYBOARD)


def _my_visits(bot, agent: Agent) -> None:
    _send_parts(bot, agent.telegram_chat_id, journal.my_visits_parts(agent))


def _places(bot, agent: Agent) -> None:
    _step(agent, STATE_PLACES_REGION)
    bot.send(agent.telegram_chat_id, "🗺 Qaysi viloyatdagi joylarni ko'ramiz?", keyboard=_region_keyboard())


def _partners(bot, agent: Agent) -> None:
    _step(agent, STATE_PARTNERS_REGION)
    bot.send(
        agent.telegram_chat_id,
        "🤝 Qaysi viloyatdagi hamkorlarimizni ko'ramiz?",
        keyboard=[[BTN_ALL_REGIONS]] + _region_keyboard(),
    )


def _ask_outcome(bot, agent: Agent) -> None:
    _step(agent, STATE_VISIT_OUTCOME)
    bot.send(agent.telegram_chat_id, "4/5. Natija qanday bo'ldi?", keyboard=OUTCOME_KEYBOARD)


def _continue_journal(bot, agent: Agent, text: str) -> None:
    chat_id = agent.telegram_chat_id
    state = agent.bot_state
    draft = agent.bot_draft or {}

    if state == STATE_PARTNERS_REGION and text == BTN_ALL_REGIONS:
        _reset_journal(agent)
        _send_parts(bot, chat_id, journal.partners_parts())
        return

    if state in (STATE_VISIT_REGION, STATE_SEARCH_REGION, STATE_PLACES_REGION, STATE_PARTNERS_REGION):
        region = match_region(text)
        if not region:
            bot.send(chat_id, "Viloyatni pastdagi tugmalardan tanlang 👇", keyboard=_region_keyboard())
            return
        if state in (STATE_PLACES_REGION, STATE_PARTNERS_REGION):
            _reset_journal(agent)
            parts = journal.places_parts(region) if state == STATE_PLACES_REGION else journal.partners_parts(region)
            _send_parts(bot, chat_id, parts)
            return
        if state == STATE_SEARCH_REGION:
            _step(agent, STATE_SEARCH_QUERY, region=region)
            bot.send(
                chat_id,
                "Restoran nomini yozing (lotincha, bir qismi ham bo'ladi — masalan «rayh») "
                f"yoki «{BTN_ALL}» — shu viloyatdagi oxirgi tashriflar.",
                keyboard=[[BTN_ALL], [BTN_CANCEL]],
            )
            return
        _step(agent, STATE_VISIT_NAME, region=region)
        bot.send(
            chat_id,
            "2/5. Restoran nomini <b>lotin harflarida</b> yozing, masalan: Oqtepa Lavash",
            keyboard=[[BTN_CANCEL]],
        )
        return

    if state == STATE_SEARCH_QUERY:
        query = "" if text == BTN_ALL else text.strip()
        if query and not journal.place_key(query):
            bot.send(chat_id, "Lotin harflarida yozing, masalan: rayhon", keyboard=[[BTN_ALL], [BTN_CANCEL]])
            return
        region = draft.get("region", "")
        _reset_journal(agent)
        _send_parts(bot, chat_id, [journal.search_text(region, query)])
        return

    if state == STATE_VISIT_NAME:
        name = journal.clean_latin(text)
        if not name:
            bot.send(
                chat_id,
                "Nomni faqat <b>lotin harflarida</b> yozing (2–60 belgi), masalan: Rayhon",
                keyboard=[[BTN_CANCEL]],
            )
            return
        existing = journal.same_name_places(draft["region"], name)
        if existing:
            _step(agent, STATE_VISIT_PICK, name=name, candidates={p.address: p.pk for p in existing[:8]})
            cards = "\n\n".join(journal.place_card(place, visits_limit=2) for place in existing[:5])
            bot.send(
                chat_id,
                f"Bu viloyatda «{name}» nomli joy(lar) bor:\n\n{cards}\n\n"
                "3/5. Qaysi biriga bordingiz? Manzilni tanlang yoki yangi manzil qo'shing.",
                keyboard=[[p.address] for p in existing[:8]] + [[BTN_NEW_ADDRESS], [BTN_CANCEL]],
            )
            return
        _step(agent, STATE_VISIT_ADDRESS, name=name)
        _ask_address(bot, agent)
        return

    if state == STATE_VISIT_PICK:
        if text == BTN_NEW_ADDRESS:
            _step(agent, STATE_VISIT_ADDRESS)
            _ask_address(bot, agent)
            return
        place_id = (draft.get("candidates") or {}).get(text)
        place = Place.objects.filter(pk=place_id).first() if place_id else None
        if place is None:
            bot.send(chat_id, "Pastdagi manzillardan birini tanlang 👇")
            return
        _step(agent, STATE_VISIT_OUTCOME, address=place.address)
        _warn_if_taken(bot, agent, place)
        _ask_outcome(bot, agent)
        return

    if state == STATE_VISIT_ADDRESS:
        address = journal.clean_latin(text, min_len=3, max_len=120)
        if not address:
            bot.send(
                chat_id,
                "Manzil yoki mo'ljalni <b>lotin harflarida</b> yozing, masalan: Chilonzor 9-kvartal",
                keyboard=[[BTN_CANCEL]],
            )
            return
        _step(agent, STATE_VISIT_OUTCOME, address=address)
        _ask_outcome(bot, agent)
        return

    if state == STATE_VISIT_OUTCOME:
        outcome = next((value for value, label in Visit.Outcome.choices if label == text), "")
        if not outcome:
            bot.send(chat_id, "Natijani pastdagi tugmalardan tanlang 👇", keyboard=OUTCOME_KEYBOARD)
            return
        _step(agent, STATE_VISIT_COMMENT, outcome=outcome)
        bot.send(
            chat_id,
            "5/5. Qisqa izoh yozing — boshqa agentlarga foydali bo'ladi.\n"
            "Masalan: «Egasi dushanba qaytadi», «Narx qimmat dedi».",
            keyboard=[[BTN_SKIP], [BTN_CANCEL]],
        )
        return

    if state == STATE_VISIT_COMMENT:
        comment = "" if text == BTN_SKIP else " ".join(text.split())[:300]
        visit = journal.record_visit(
            agent,
            region=draft["region"],
            name=draft["name"],
            address=draft["address"],
            outcome=draft["outcome"],
            comment=comment,
        )
        _reset_journal(agent)
        note = ""
        if visit.outcome == Visit.Outcome.INTERESTED:
            note = f"\n\n🟡 Joy sizga {journal.RESERVE_DAYS} kun band — boshqa agentlar buni ko'radi."
        bot.send(
            chat_id,
            "✅ Yozildi.\n\n" + journal.place_card(visit.place) + note,
            keyboard=KEYBOARD,
        )
        return

    _reset_journal(agent)


def _ask_address(bot, agent: Agent) -> None:
    bot.send(
        agent.telegram_chat_id,
        "3/5. Manzil yoki mo'ljalni <b>lotin harflarida</b> yozing, masalan: Chilonzor 9-kvartal",
        keyboard=[[BTN_CANCEL]],
    )


def _warn_if_taken(bot, agent: Agent, place: Place) -> None:
    held = journal.reservation(place)
    if place.restaurant_id:
        bot.send(agent.telegram_chat_id, "🟢 Bu restoran allaqachon stolda.uz mijozi.")
    elif held and held.agent and held.agent.pk != agent.pk:
        bot.send(
            agent.telegram_chat_id,
            f"🟡 Bu joy bilan {journal.first_name(held.agent)} ishlayapti ({held.days_left} kun qoldi). "
            "Qoidaga ko'ra, band paytida bu joyga bormaslik kerak.",
        )


def _next_date(subscription) -> str:
    """Restoran ro'yxatida — qachongacha to'langan / sinov / imtiyoz."""
    if subscription is None:
        return ""
    when, label = {
        "trialing": (subscription.trial_ends_at, "gacha"),
        "active": (subscription.current_period_end, "gacha to'langan"),
        "past_due": (subscription.grace_ends_at, "gacha to'lashi kerak"),
    }.get(subscription.status, (None, ""))
    if not when:
        return ""
    return f"{timezone.localtime(when).strftime('%d.%m')} {label}"


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
        f"2. Restoran sinovdan keyin to'lasa — birinchi to'lovidan {agent.first_percent}%, "
        f"keyingilaridan {agent.percent}% ({services.duration(agent.months)}).\n"
        f"3. To'lov yaqinlashganda yoki kechiksa bot eslatadi — restoranga qo'ng'iroq qilib "
        f"eslatib qo'ying. Restoran to'lab tursa, siz ham har oy daromad olasiz.\n"
        f"4. Balans {services.money(services.MIN_WITHDRAWAL)} dan oshsa — «{BTN_WITHDRAW}».\n\n"
        f"5. Har borgan joyingizni «{BTN_VISIT}» orqali yozing. Borishdan oldin «{BTN_SEARCH}» bilan "
        f"tekshiring: 🟢 mijoz yoki 🟡 band joyga bormang.\n"
        f"6. «{BTN_MY_VISITS}» — o'zingiz borgan joylar, «{BTN_PLACES}» — hamma agentlar borgan joylar, "
        f"«{BTN_PARTNERS}» — stolda.uz'dan foydalanayotgan restoranlar.\n\n"
        f"To'liq qoidalar: /qoidalar\n"
        f"Savollar: {SUPPORT}",
        keyboard=KEYBOARD,
    )


def _rules(bot, agent: Agent) -> None:
    _show_rules(bot, agent.telegram_chat_id, KEYBOARD)


_BUTTONS = {
    BTN_VISIT: _visit,
    BTN_SEARCH: _search,
    BTN_MY_VISITS: _my_visits,
    BTN_PLACES: _places,
    BTN_PARTNERS: _partners,
    BTN_LINK: _link,
    BTN_BALANCE: _balance,
    BTN_RESTAURANTS: _restaurants,
    BTN_WITHDRAW: _withdraw,
    BTN_CARD: _card,
    BTN_HELP: _help,
    BTN_RULES: _rules,
}
_ALLOWED_WHEN_INACTIVE = {_balance, _withdraw, _card, _help, _my_visits, _rules}

_COMMANDS = {
    "/joy": _visit,
    "/qidirish": _search,
    "/joylarim": _my_visits,
    "/joylar": _places,
    "/hamkorlar": _partners,
    "/havola": _link,
    "/balans": _balance,
    "/restoranlar": _restaurants,
    "/yechish": _withdraw,
    "/karta": _card,
    "/yordam": _help,
    "/qoidalar": _rules,
    "/help": _help,
}

#: Telegram'dagi "Menu" tugmasi — bot ishga tushganda o'rnatiladi (`agent_bot`).
MENU_COMMANDS = [
    ("start", "Boshlash"),
    ("joy", "Borgan joyimni yozish"),
    ("joylarim", "Borgan joylarim"),
    ("qidirish", "Joyni qidirish"),
    ("joylar", "Barcha joylar"),
    ("hamkorlar", "Hamkorlarimiz"),
    ("havola", "Havolam va QR vizitka"),
    ("balans", "Balans"),
    ("restoranlar", "Restoranlarim"),
    ("yechish", "Pul yechish"),
    ("karta", "Kartani saqlash"),
    ("yordam", "Qanday ishlaydi"),
    ("qoidalar", "Agent qoidalari"),
]


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
