"""Savdo agentlari: restoran olib keladi, har to'lovdan foiz oladi.

Oqim: agent o'z havolasi (`stolda.uz/?agent=ALI`) yoki kodini beradi →
restoran shu kod bilan ro'yxatdan o'tadi (`Restaurant.agent`) → restoran
to'lagan har chekdan agentga `AgentEarning` yoziladi → agent botda
"Pul yechish" ni bosadi (`AgentWithdrawal`) → platforma egasi kartaga
o'tkazib, Telegram'da "O'tkazdim" ni bosadi.
"""

import re
import secrets

from django.core.exceptions import ValidationError
from django.db import models

CODE_RE = re.compile(r"^[A-Z0-9]{3,16}$")


def normalize_code(value: str) -> str:
    """`ali 25` → `ALI25`. Faqat lotin harflari va raqamlar."""
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def validate_code(value: str) -> None:
    if not CODE_RE.match(value or ""):
        raise ValidationError("Kod 3–16 ta lotin harfi yoki raqamdan iborat bo'lsin, masalan ALI25.")


def _invite_token() -> str:
    return secrets.token_urlsafe(12)


class Agent(models.Model):
    name = models.CharField("Ismi", max_length=80)
    phone = models.CharField("Telefon", max_length=20, blank=True)
    code = models.CharField(
        "Kodi",
        max_length=16,
        unique=True,
        validators=[validate_code],
        help_text="Havola va ro'yxatdan o'tishda kiritiladi: ALI, JASUR25…",
    )
    is_active = models.BooleanField(
        "Faol", default=True, help_text="O'chirilsa yangi restoran ham, yangi daromad ham yozilmaydi."
    )

    # Shartlar — hammaga bir xil sukut, kerak bo'lsa agentga alohida.
    percent = models.PositiveSmallIntegerField("Har to'lovdan, %", default=20)
    months = models.PositiveSmallIntegerField(
        "Necha oy davomida", default=12, help_text="Restoranning birinchi to'lovidan boshlab."
    )
    first_bonus = models.PositiveIntegerField("Birinchi to'lov bonusi, so'm", default=30_000)

    # Telegram — agent taklif havolasini bosganda ulanadi.
    invite_token = models.CharField(max_length=32, unique=True, default=_invite_token, editable=False)
    telegram_chat_id = models.CharField(max_length=32, blank=True, db_index=True, editable=False)
    telegram_username = models.CharField(max_length=64, blank=True, editable=False)
    #: Botdagi suhbat bosqichi (karta raqamini kutyapmizmi va h.k.).
    bot_state = models.CharField(max_length=16, blank=True, editable=False)

    card_number = models.CharField("Karta raqami", max_length=19, blank=True)
    card_holder = models.CharField("Karta egasi", max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "agent"
        verbose_name_plural = "agentlar"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        self.code = normalize_code(self.code)
        super().save(*args, **kwargs)


class AgentWithdrawal(models.Model):
    """Agentning pul yechish so'rovi — platforma egasi qo'lda o'tkazadi."""

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        PAID = "paid", "O'tkazildi"
        REJECTED = "rejected", "Rad etildi"

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="withdrawals")
    amount = models.PositiveIntegerField("Summa, so'm")
    #: So'rov paytidagi karta — keyin o'zgartirilsa ham qaysi kartaga ekani qoladi.
    card_number = models.CharField(max_length=19)
    card_holder = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=200, blank=True)
    telegram_message_id = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "pul yechish"
        verbose_name_plural = "pul yechishlar"
        constraints = [
            # Bir vaqtda bitta kutilayotgan so'rov.
            models.UniqueConstraint(
                fields=["agent"],
                condition=models.Q(status="pending"),
                name="one_pending_withdrawal_per_agent",
            )
        ]

    def __str__(self) -> str:
        return f"{self.agent.code} · {self.amount} so'm · {self.get_status_display()}"


class AgentEarning(models.Model):
    """Agentga tegishli summa — restoran to'lovi tasdiqlanganda yoziladi."""

    class Kind(models.TextChoices):
        BONUS = "bonus", "Birinchi to'lov bonusi"
        PERCENT = "percent", "Foiz"

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="earnings")
    restaurant = models.ForeignKey(
        "menu.Restaurant", on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_earnings"
    )
    #: Restoran o'chirilsa ham hisobotda nomi qolsin.
    restaurant_name = models.CharField(max_length=120)
    invoice = models.ForeignKey(
        "menu.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_earnings"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.PositiveIntegerField("Summa, so'm")
    #: Qaysi pul yechishga kirgan. Bo'sh — hali yechilmagan (balansda).
    withdrawal = models.ForeignKey(
        AgentWithdrawal, on_delete=models.SET_NULL, null=True, blank=True, related_name="earnings"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "agent daromadi"
        verbose_name_plural = "agent daromadlari"
        constraints = [
            # Bir to'lovdan bir turdagi daromad bir marta — tasdiq ikki marta bosilsa ham.
            models.UniqueConstraint(
                fields=["invoice", "kind"],
                condition=models.Q(invoice__isnull=False),
                name="one_earning_per_invoice_kind",
            )
        ]

    def __str__(self) -> str:
        return f"{self.agent.code} · {self.restaurant_name} · {self.amount} so'm"
