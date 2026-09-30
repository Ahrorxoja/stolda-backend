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

from menu.regions import REGION_CHOICES

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
    #: Ariza hali tasdiqlanmagan bo'lsa bo'sh — kod qabul qilinganda beriladi.
    code = models.CharField(
        "Kodi",
        max_length=16,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_code],
        help_text="Havola va ro'yxatdan o'tishda kiritiladi: ALI, JASUR25…",
    )
    is_active = models.BooleanField(
        "Faol", default=True, help_text="O'chirilsa yangi restoran ham, yangi daromad ham yozilmaydi."
    )
    #: Viloyat (botdagi ro'yxatdan) — `menu/regions.py: REGIONS`.
    city = models.CharField("Viloyat", max_length=60, blank=True)
    #: Arizadagi "restoranlar bilan tanishlaringiz bormi?" javobi.
    note = models.CharField("Ariza izohi", max_length=300, blank=True)
    #: Bot orqali ariza — `applied_at` bor, `approved_at`/`rejected_at` hali yo'q.
    #: Django admin'da qo'lda qo'shilgan agentda uchalasi ham bo'sh.
    applied_at = models.DateTimeField("Ariza sanasi", null=True, blank=True, editable=False)
    #: Qaysi qoidalarga (`agents/rules.py: RULES_VERSION`) va qachon rozi bo'lgan.
    rules_version = models.CharField("Qoidalar versiyasi", max_length=10, blank=True, editable=False)
    rules_accepted_at = models.DateTimeField("Qoidalarga rozi", null=True, blank=True, editable=False)
    approved_at = models.DateTimeField("Qabul qilingan", null=True, blank=True, editable=False)
    rejected_at = models.DateTimeField("Rad etilgan", null=True, blank=True, editable=False)

    # Shartlar — hammaga bir xil sukut, kerak bo'lsa agentga alohida.
    percent = models.PositiveSmallIntegerField("Keyingi to'lovlardan, %", default=20)
    #: 0 — cheksiz: agent faol ekan, restoran to'lagan har oydan. Faolligi
    #: o'chirilsa (ishdan ketdi, mas'uliyatsizlik) — yangi daromad to'xtaydi.
    months = models.PositiveSmallIntegerField(
        "Necha oy davomida",
        default=0,
        help_text="0 — cheksiz (agent faol ekan). Aks holda birinchi to'lovdan boshlab shuncha oy.",
    )
    #: Restoranning birinchi to'lovidan (oylik yoki yillik — farqi yo'q).
    first_percent = models.PositiveSmallIntegerField("Birinchi to'lovdan, %", default=50)

    # Telegram — agent taklif havolasini bosganda ulanadi.
    invite_token = models.CharField(max_length=32, unique=True, default=_invite_token, editable=False)
    telegram_chat_id = models.CharField(max_length=32, blank=True, db_index=True, editable=False)
    telegram_username = models.CharField(max_length=64, blank=True, editable=False)
    #: Botdagi suhbat bosqichi (karta raqamini kutyapmizmi va h.k.).
    bot_state = models.CharField(max_length=16, blank=True, editable=False)
    #: Ko'p qadamli yozuv (borgan joy, qidiruv) tugaguncha oraliq qiymatlar.
    bot_draft = models.JSONField(default=dict, blank=True, editable=False)

    card_number = models.CharField("Karta raqami", max_length=19, blank=True)
    card_holder = models.CharField("Karta egasi", max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "agent"
        verbose_name_plural = "agentlar"

    def __str__(self) -> str:
        return f"{self.name} ({self.code or 'ariza'})"

    def clean(self) -> None:
        # Qo'lda qo'shilgan yoki qabul qilingan faol agentga kod shart.
        if self.is_active and not normalize_code(self.code or "") and not self.is_pending:
            raise ValidationError({"code": "Faol agentga kod kerak, masalan ALI."})

    @property
    def is_pending(self) -> bool:
        """Bot orqali ariza topshirgan, hali javob berilmagan."""
        return bool(self.applied_at) and not self.approved_at and not self.rejected_at

    def save(self, *args, **kwargs):
        # Bo'sh kod `NULL` bo'lsin — `unique` bir nechta arizaga xalaqit bermasin.
        self.code = normalize_code(self.code or "") or None
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
        FIRST = "first", "Birinchi to'lov"
        PERCENT = "percent", "Keyingi to'lov"

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


# ── Borilgan joylar jurnali ────────────────────────────────────────────


def place_key(value: str) -> str:
    """Takrorni aniqlash uchun: "Oqtepa  Lavash'" → `oqtepalavash`."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


class Place(models.Model):
    """Agentlar borgan restoran — hamma agentlar ko'radigan umumiy jurnal.

    Bir viloyatda nomi va manzili bir xil bo'lsa — bitta joy, tashriflar
    uning tarixiga qo'shiladi.
    """

    region = models.CharField("Viloyat", max_length=30, choices=REGION_CHOICES)
    name = models.CharField("Nomi", max_length=60)
    address = models.CharField("Manzil / mo'ljal", max_length=120)
    name_key = models.CharField(max_length=60, db_index=True, editable=False)
    address_key = models.CharField(max_length=120, editable=False)
    #: Ulangach — stolda.uz'dagi restoran.
    restaurant = models.ForeignKey(
        "menu.Restaurant", on_delete=models.SET_NULL, null=True, blank=True, related_name="places"
    )
    created_by = models.ForeignKey(
        Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="places_added"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("region", "name")
        verbose_name = "borilgan joy"
        verbose_name_plural = "borilgan joylar"
        constraints = [
            models.UniqueConstraint(fields=["region", "name_key", "address_key"], name="one_place_per_address")
        ]

    def __str__(self) -> str:
        return f"{self.name} — {self.address} ({self.region})"

    def save(self, *args, **kwargs):
        self.name_key = place_key(self.name)
        self.address_key = place_key(self.address)
        super().save(*args, **kwargs)


class Visit(models.Model):
    class Outcome(models.TextChoices):
        INTERESTED = "interested", "🤝 Qiziqdi, qayta boraman"
        CONNECTED = "connected", "✅ Ulandi"
        OTHER_QR = "other_qr", "📱 Boshqa QR menyusi bor"
        REFUSED = "refused", "❌ Rad etdi"
        NO_OWNER = "no_owner", "🚪 Egasi yo'q edi"

    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="visits")
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="visits")
    outcome = models.CharField("Natija", max_length=12, choices=Outcome.choices)
    comment = models.CharField("Izoh", max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "tashrif"
        verbose_name_plural = "tashriflar"

    def __str__(self) -> str:
        return f"{self.place.name} · {self.get_outcome_display()}"

