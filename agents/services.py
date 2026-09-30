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


def agent_link(agent: Agent) -> str:
    return f"{settings.SITE_URL}/?agent={agent.code}"


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


def notify_agent(agent: Agent, text: str) -> None:
    """Agentga xabar — tranzaksiyadan keyin, xatosi yutiladi."""
    if not agent.telegram_chat_id:
        return
    chat_id = agent.telegram_chat_id

    def send():
        try:
            get_agent_bot().send(chat_id, text)
        except TelegramError as error:
            logger.warning("Agentga xabar yuborilmadi (%s): %s", agent.code, error)

    transaction.on_commit(send)


# ── Biriktirish ────────────────────────────────────────────────────────


def attach(restaurant, agent: Agent) -> None:
    """Restoranni agentga yozadi (faqat hali biriktirilmagan bo'lsa)."""
    if restaurant.agent_id:
        return
    restaurant.agent = agent
    restaurant.agent_attached_at = timezone.now()
    restaurant.save(update_fields=["agent", "agent_attached_at"])
    notify_agent(
        agent,
        f"🎉 <b>{restaurant.name}</b> sizning kodingiz bilan ro'yxatdan o'tdi.\n"
        f"Sinov muddati tugab, birinchi to'lov tasdiqlangach daromadingiz yoziladi.",
    )


# ── Daromad ────────────────────────────────────────────────────────────


def on_invoice_paid(invoice) -> list[AgentEarning]:
    """To'lov tasdiqlanganda chaqiriladi (`menu.billing_ledger.record_payment`).

    Idempotent: bir to'lovdan bir turdagi daromad bir marta yoziladi.
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
    window_end = first.paid_at + timedelta(days=agent.months * DAYS_PER_MONTH)
    if invoice.paid_at > window_end:
        return []

    created = []
    rows = [(AgentEarning.Kind.PERCENT, invoice.amount * agent.percent // 100)]
    if invoice.pk == first.pk and agent.first_bonus:
        rows.append((AgentEarning.Kind.BONUS, agent.first_bonus))
    for kind, amount in rows:
        if amount <= 0:
            continue
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
        if was_created:
            created.append(earning)

    if created:
        total = sum(earning.amount for earning in created)
        notify_agent(
            agent,
            f"💰 <b>{restaurant.name}</b> to'lov qildi — sizga <b>+{money(total)}</b>.\n"
            f"Balans: {money(balance(agent).available)}",
        )
    return created


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
