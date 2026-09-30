"""Agentlar bo'yicha yagona mantiq: biriktirish, daromad, balans, pul yechish.

Xabarlar (agentga — agentlar botidan, platforma egasiga — cheklar botidan)
tranzaksiya muvaffaqiyatli tugagach yuboriladi va xatosi asosiy oqimni
to'xtatmaydi: Telegram ishlamasa ham to'lov va hisob-kitob to'g'ri yoziladi.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from telegrambot import TelegramError, get_agent_bot, get_bot

from .models import Agent, AgentEarning, AgentWithdrawal, normalize_code

logger = logging.getLogger(__name__)

#: Bundan kam summani yechib bo'lmaydi — mayda o'tkazmalar bilan ovora bo'lmaslik uchun.
MIN_WITHDRAWAL = 100_000
#: Agent kodi bilan kelgan restoranga sinov muddati (odatdagisi 14 kun).
AGENT_TRIAL_DAYS = 21
#: "Oy" — foiz muddatini hisoblash uchun.
DAYS_PER_MONTH = 30


def money(amount: int) -> str:
    """`150000` → `150 000 so'm`."""
    return f"{amount:,}".replace(",", " ") + " so'm"


def terms(agent: Agent) -> str:
    """Shartlar matni — bot va xabarlarda bir xil."""
    return (
        f"Restoran sizning havolangiz yoki kodingiz bilan ro'yxatdan o'tsa, uning "
        f"<b>birinchi to'lovidan {agent.first_percent}%</b> (oylik yoki yillik) va "
        f"<b>keyingi to'lovlaridan {agent.percent}%</b> ({duration(agent.months)}) olasiz."
    )


def duration(months: int) -> str:
    return f"{months} oy davomida" if months else "restoran to'lashda davom etar ekan"


def agent_link(agent: Agent) -> str:
    # To'g'ridan-to'g'ri ro'yxatdan o'tishga — restoran egasi shu yerning o'zida Google bilan kiradi.
    return f"{settings.SITE_URL}/signup?agent={agent.code}"


def invite_link(agent: Agent) -> str:
    """Agent botni shu havola orqali ochsa, bot uni taniydi."""
    username = settings.AGENT_BOT_USERNAME
    return f"https://t.me/{username}?start={agent.invite_token}" if username else ""


def find_agent(code: str) -> Agent | None:
    code = normalize_code(code)
    if not code:
        return None
    return Agent.objects.filter(code=code, is_active=True).first()


# ── Xabarlar ───────────────────────────────────────────────────────────


def notify_agent(agent: Agent, text: str, *, main_keyboard: bool = False) -> None:
    """Agentga xabar — tranzaksiyadan keyin, xatosi yutiladi.

    `main_keyboard` — pastdagi asosiy tugmalarni ham yuborish (qabul qilinganda).
    """
    if not agent.telegram_chat_id:
        return
    chat_id = agent.telegram_chat_id

    def send():
        from .bot import KEYBOARD  # bot.py services'ni import qiladi — aylanma import bo'lmasin

        try:
            get_agent_bot().send(chat_id, text, keyboard=KEYBOARD if main_keyboard else None)
        except TelegramError as error:
            logger.warning("Agentga xabar yuborilmadi (%s): %s", agent.code, error)

    transaction.on_commit(send)


# ── Ariza (agent o'zi to'ldiradi, platforma egasi tasdiqlaydi) ─────────


# Kirill ismlardan ham kod chiqsin: "Алишер" → "ALISHER".
_CYRILLIC = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu",
        "я": "ya", "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
    }
)


def generate_code(name: str) -> str:
    """Ismdan oson eslanadigan kod: "Ali Valiyev" → `ALI`, band bo'lsa `ALI2`, `ALI3`…"""
    first = (name.split() or [""])[0].lower().translate(_CYRILLIC)
    base = normalize_code(first)[:8]
    if len(base) < 3:
        base = (base + "AGENT")[:5]
    candidate, number = base, 1
    while Agent.objects.filter(code=candidate).exists():
        number += 1
        candidate = f"{base}{number}"
    return candidate


def submit_application(agent: Agent) -> None:
    """Ariza to'ldirildi — platforma egasiga "Qabul / Rad" tugmalari bilan yuboriladi."""
    agent.applied_at = timezone.now()
    agent.save(update_fields=["applied_at"])
    agent_id = agent.pk

    def send():
        fresh = Agent.objects.get(pk=agent_id)
        text = (
            f"🙋 <b>Yangi agent arizasi</b>\n\n"
            f"Ism: <b>{fresh.name}</b>\n"
            f"Telefon: {fresh.phone or '—'}\n"
            f"Viloyat: {fresh.city or '—'}\n"
            f"Telegram: {'@' + fresh.telegram_username if fresh.telegram_username else '—'}\n"
            f"Tanishlar: {fresh.note or '—'}"
        )
        try:
            get_bot().send_buttons(
                text, [("✅ Qabul qilish", f"ag_ok:{fresh.pk}"), ("❌ Rad etish", f"ag_no:{fresh.pk}")]
            )
        except TelegramError as error:
            logger.warning("Agent arizasi Telegram'ga yuborilmadi: %s", error)

    transaction.on_commit(send)


@transaction.atomic
def approve_application(agent: Agent) -> bool:
    """Arizani qabul qiladi: kod beradi, faollashtiradi, agentga xabar. Qayta bosilsa `False`."""
    fresh = Agent.objects.select_for_update().get(pk=agent.pk)
    if not fresh.is_pending:
        return False
    fresh.code = generate_code(fresh.name)
    fresh.is_active = True
    fresh.approved_at = timezone.now()
    fresh.save(update_fields=["code", "is_active", "approved_at"])
    notify_agent(
        fresh,
        f"🎉 Tabriklaymiz, <b>{fresh.name}</b>! Arizangiz qabul qilindi — siz stolda.uz agentisiz.\n\n"
        f"Kodingiz: <b>{fresh.code}</b>\n"
        f"{terms(fresh)}\n\n"
        f"Boshlash uchun «🔗 Havolam» ni bosing.",
        main_keyboard=True,
    )
    return True


@transaction.atomic
def reject_application(agent: Agent) -> bool:
    fresh = Agent.objects.select_for_update().get(pk=agent.pk)
    if not fresh.is_pending:
        return False
    fresh.rejected_at = timezone.now()
    fresh.is_active = False
    fresh.save(update_fields=["rejected_at", "is_active"])
    notify_agent(
        fresh,
        "Rahmat, arizangiz ko'rib chiqildi. Afsuski, hozircha qabul qila olmaymiz. "
        "Savollar bo'lsa: @aha_daragoy",
    )
    return True


# ── Biriktirish ────────────────────────────────────────────────────────


def attach(restaurant, agent: Agent) -> None:
    """Restoranni agentga yozadi (faqat hali biriktirilmagan bo'lsa)."""
    if restaurant.agent_id:
        return
    restaurant.agent = agent
    restaurant.agent_attached_at = timezone.now()
    restaurant.save(update_fields=["agent", "agent_attached_at"])
    from .journal import link_restaurant  # journal → menu.models; aylanma import bo'lmasin

    link_restaurant(restaurant, agent)
    notify_agent(
        agent,
        f"🎉 <b>{restaurant.name}</b> sizning kodingiz bilan ro'yxatdan o'tdi.\n"
        f"Sinov muddati tugab, birinchi to'lov tasdiqlangach daromadingiz yoziladi.",
    )


# ── Daromad ────────────────────────────────────────────────────────────


def on_invoice_paid(invoice) -> list[AgentEarning]:
    """To'lov tasdiqlanganda chaqiriladi (`menu.billing_ledger.record_payment`).

    Idempotent: bir to'lovdan daromad bir marta yoziladi.
    """
    subscription = invoice.subscription
    restaurant = subscription.restaurant
    agent = restaurant.agent
    if agent is None or not agent.is_active or not invoice.paid_at:
        return []

    first = (
        subscription.invoices.filter(status="paid", paid_at__isnull=False)
        .order_by("paid_at", "pk")
        .first()
    )
    if first is None:
        return []

    # Birinchi to'lov (oylik yoki yillik) — `first_percent`; keyingilari —
    # `percent`: agent faol ekan cheksiz, `months` berilgan bo'lsa shuncha oy.
    if invoice.pk == first.pk:
        kind, percent = AgentEarning.Kind.FIRST, agent.first_percent
    else:
        if agent.months:
            window_end = first.paid_at + timedelta(days=agent.months * DAYS_PER_MONTH)
            if invoice.paid_at > window_end:
                return []
        kind, percent = AgentEarning.Kind.PERCENT, agent.percent

    amount = invoice.amount * percent // 100
    if amount <= 0:
        return []
    earning, was_created = AgentEarning.objects.get_or_create(
        invoice=invoice,
        kind=kind,
        defaults={
            "agent": agent,
            "restaurant": restaurant,
            "restaurant_name": restaurant.name,
            "amount": amount,
        },
    )
    if not was_created:
        return []

    notify_agent(
        agent,
        f"💰 <b>{restaurant.name}</b> to'lov qildi — sizga <b>+{money(amount)}</b>.\n"
        f"Balans: {money(balance(agent).available)}",
    )
    return [earning]


# ── Eslatmalar (kunlik obuna tekshiruvidan) ────────────────────────────


REMINDERS = {
    "trial_soon": "⏰ <b>{name}</b> — bepul sinov {date} da tugaydi. Birinchi to'lovni eslatib qo'ying.",
    "due_soon": "⏰ <b>{name}</b> — to'lov muddati {date} da tugaydi. Eslatib qo'ying.",
    "past_due": "💳 <b>{name}</b> — to'lov muddati o'tdi. 7 kun ichida to'lasa, menyu to'xtamaydi.",
    "suspended": "⛔️ <b>{name}</b> — to'lanmagani uchun menyu to'xtatildi. To'lasa darhol tiklanadi.",
}


def reminder_line(subscription, kind: str, when=None) -> str:
    """Agentga bitta restoran haqida — qo'ng'iroq qilish uchun telefonlari bilan."""
    restaurant = subscription.restaurant
    profile = getattr(restaurant.owner, "profile", None)
    phones = []
    for phone in (restaurant.phone, getattr(profile, "contact_phone", "")):
        if phone and format_phone(phone) not in phones:
            phones.append(format_phone(phone))
    date = timezone.localtime(when).strftime("%d.%m") if when else ""
    line = REMINDERS[kind].format(name=restaurant.name, date=date)
    return line + (f"\n📞 {', '.join(phones)}" if phones else "")


def format_phone(phone: str) -> str:
    """`+998901234567` → `+998 90 123 45 67` (boshqa ko'rinishdagisi o'zicha)."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 12 and digits.startswith("998"):
        return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:12]}"
    return phone


def remind_agents(events: dict[int, list[str]]) -> None:
    """Har agentga bitta xabar — bugun e'tibor kerak bo'lgan restoranlari."""
    if not events:
        return
    for agent in Agent.objects.filter(pk__in=events, is_active=True).exclude(telegram_chat_id=""):
        notify_agent(agent, "📋 <b>Restoranlaringiz bo'yicha eslatma</b>\n\n" + "\n\n".join(events[agent.pk]))


# ── Balans ─────────────────────────────────────────────────────────────


@dataclass
class Balance:
    #: Yechish mumkin — hali hech qanday so'rovga kirmagan.
    available: int
    #: So'rov yuborilgan, platforma egasi hali o'tkazmagan.
    pending: int
    #: Kartaga o'tkazib bo'lingan.
    paid: int
    #: Joriy oyda yozilgan.
    this_month: int


def balance(agent: Agent) -> Balance:
    earnings = AgentEarning.objects.filter(agent=agent)

    def total(queryset) -> int:
        return queryset.aggregate(total=Sum("amount"))["total"] or 0

    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return Balance(
        available=total(earnings.filter(withdrawal__isnull=True)),
        pending=total(earnings.filter(withdrawal__status=AgentWithdrawal.Status.PENDING)),
        paid=total(earnings.filter(withdrawal__status=AgentWithdrawal.Status.PAID)),
        this_month=total(earnings.filter(created_at__gte=month_start)),
    )


# ── Pul yechish ────────────────────────────────────────────────────────


class WithdrawalError(Exception):
    """Agentga ko'rsatiladigan tushunarli sabab bilan."""


def request_withdrawal(agent: Agent) -> AgentWithdrawal:
    """Balansdagi hamma pulni bitta so'rovga yig'ib, platforma egasiga yuboradi."""
    if not agent.card_number:
        raise WithdrawalError("Avval kartangizni kiriting: 💳 Karta tugmasini bosing.")

    with transaction.atomic():
        locked = Agent.objects.select_for_update().get(pk=agent.pk)
        if AgentWithdrawal.objects.filter(agent=locked, status=AgentWithdrawal.Status.PENDING).exists():
            raise WithdrawalError("Oldingi so'rovingiz hali ko'rib chiqilmoqda. Tez orada javob keladi.")
        earnings = list(
            AgentEarning.objects.select_for_update().filter(agent=locked, withdrawal__isnull=True)
        )
        amount = sum(earning.amount for earning in earnings)
        if amount < MIN_WITHDRAWAL:
            raise WithdrawalError(
                f"Yechish uchun kamida {money(MIN_WITHDRAWAL)} kerak. Hozir balansda: {money(amount)}."
            )
        withdrawal = AgentWithdrawal.objects.create(
            agent=locked,
            amount=amount,
            card_number=locked.card_number,
            card_holder=locked.card_holder,
        )
        AgentEarning.objects.filter(pk__in=[earning.pk for earning in earnings]).update(
            withdrawal=withdrawal
        )
        transaction.on_commit(lambda: _send_to_owner(withdrawal.pk))
    return withdrawal


def _send_to_owner(withdrawal_id: int) -> None:
    """Platforma egasiga — karta raqami to'liq, "O'tkazdim"/"Rad etish" tugmalari bilan."""
    withdrawal = AgentWithdrawal.objects.select_related("agent").get(pk=withdrawal_id)
    agent = withdrawal.agent
    text = (
        f"💸 <b>Pul yechish so'rovi</b>\n\n"
        f"Agent: <b>{agent.name}</b> ({agent.code})\n"
        f"Summa: <b>{money(withdrawal.amount)}</b>\n"
        f"Karta: <code>{withdrawal.card_number}</code>\n"
        f"Egasi: {withdrawal.card_holder or '—'}\n"
        f"Telefon: {agent.phone or '—'}"
    )
    try:
        message_id = get_bot().send_buttons(
            text,
            [("✅ O'tkazdim", f"wd_paid:{withdrawal.pk}"), ("❌ Rad etish", f"wd_reject:{withdrawal.pk}")],
        )
    except TelegramError as error:
        logger.warning("Pul yechish so'rovi Telegram'ga yuborilmadi: %s", error)
        return
    AgentWithdrawal.objects.filter(pk=withdrawal.pk).update(telegram_message_id=message_id)


@transaction.atomic
def settle_withdrawal(withdrawal: AgentWithdrawal, *, paid: bool, note: str = "") -> bool:
    """"O'tkazdim" yoki "Rad etish". Faqat kutilayotgan so'rov uchun — qayta bosilsa `False`.

    Rad etilsa daromadlar balansga qaytadi (keyin qayta so'rash mumkin).
    """
    fresh = AgentWithdrawal.objects.select_for_update().select_related("agent").get(pk=withdrawal.pk)
    if fresh.status != AgentWithdrawal.Status.PENDING:
        return False

    fresh.status = AgentWithdrawal.Status.PAID if paid else AgentWithdrawal.Status.REJECTED
    fresh.note = note
    fresh.processed_at = timezone.now()
    fresh.save(update_fields=["status", "note", "processed_at"])

    if paid:
        notify_agent(
            fresh.agent,
            f"✅ <b>{money(fresh.amount)}</b> kartangizga o'tkazildi.\n"
            f"Karta: {mask_card(fresh.card_number)}",
        )
    else:
        fresh.earnings.update(withdrawal=None)
        notify_agent(
            fresh.agent,
            f"❌ Pul yechish so'rovi rad etildi{': ' + note if note else '.'}\n"
            f"Pul balansingizga qaytdi. Savol bo'lsa: @aha_daragoy",
        )
    return True


def mask_card(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else number
