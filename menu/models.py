"""stolda.uz ma'lumotlar modeli.

Menyu faqat ko'rsatish uchun: buyurtma, savat va to'lov yo'q.
Statistika ham faqat ko'rishlardan iborat (MenuView).
"""


import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import qr as qr_codes
from .hours import default_working_hours, validate_working_hours
from .phones import validate_phone_list
from .regions import REGION_CHOICES
from .translations import (
    DEFAULT_LANGUAGE,
    LANGUAGE_NAMES,
    default_languages,
    empty_translation,
    empty_translation_list,
    validate_languages,
    validate_translation,
    validate_translation_list,
    validate_translation_meta,
)


class Unit(models.TextChoices):
    GRAM = "g", "g"
    MILLILITER = "ml", "ml"


class Badge(models.TextChoices):
    POPULAR = "popular", "Mashhur"
    VEG = "veg", "Vegetarian"


class ViewKind(models.TextChoices):
    SCAN = "scan", "QR skaner"
    DISH_OPEN = "dish_open", "Taom ochildi"


class PlatformSettings(models.Model):
    """Platformaning o'z sozlamalari — bitta yozuv, Django admin orqali.

    Karta raqami va yordam aloqalari `.env` da turganda har o'zgarishda
    serverni qayta ishga tushirish kerak edi. Endi ular bazada: admin
    panelidan o'zgartirilgan zahoti to'lov sahifasida ko'rinadi.

    Bo'sh qoldirilgan maydon uchun `.env` dagi qiymat ishlatiladi —
    shuning uchun eski sozlama ham buzilmaydi.
    """

    card_number = models.CharField("Karta raqami", max_length=32, blank=True)
    card_holder = models.CharField("Karta egasi", max_length=120, blank=True)
    #: To'lovda muammo chiqqan restoran egasi shu yerga murojaat qiladi.
    support_phone = models.CharField("Yordam telefoni", max_length=32, blank=True)
    support_telegram = models.CharField("Yordam Telegrami", max_length=120, blank=True)
    #: To'lov sahifasida Click tugmasi ko'rinadimi. Kalitlarga bog'liq emas —
    #: kalitsiz ham dizaynni ko'rish mumkin, bosilganda "ulanmagan" deydi.
    show_click_button = models.BooleanField(
        "Click tugmasini ko'rsatish",
        default=False,
        help_text="Yoqilsa to'lov sahifasida «Click orqali to'lash» tugmasi chiqadi. "
        "To'lov ishlashi uchun serverda CLICK_* kalitlari ham kerak (CLICK.md).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platforma sozlamalari"
        verbose_name_plural = "Platforma sozlamalari"

    def __str__(self) -> str:
        return "Platforma sozlamalari"

    def save(self, *args, **kwargs):
        # Yozuv doim bitta bo'lsin. `objects.create()` `force_insert` bilan
        # keladi — ikkinchi chaqiruvda u baza xatosiga olib kelardi, shuning
        # uchun olib tashlanadi va mavjud yozuv yangilanadi.
        self.pk = 1
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover — adminda taqiqlangan
        pass

    @classmethod
    def load(cls) -> "PlatformSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Profile(models.Model):
    """Hisob egasining aloqa ma'lumotlari.

    Mijozga ko'rinmaydi — bu platforma egasi restoran egasi bilan bog'lanishi
    uchun: to'lov kechikkanda yoki biror muammo chiqqanda. Restoranning
    ommaviy telefoni (`Restaurant.phone`) bilan aralashtirmaslik kerak.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    full_name = models.CharField(max_length=120, blank=True)
    #: Bir ko'rinishga keltirilgan raqam — `phones.normalize_phone`.
    contact_phone = models.CharField(max_length=32, blank=True)
    #: `@nom` yoki to'liq havola.
    telegram = models.CharField(max_length=120, blank=True)
    #: @Stoldabot'ga ulangan Telegram hisobi — Mini App shu bilan parolsiz kiradi
    #: va restoran xabarlari shu chatga boradi (`menu/telegram_link.py`).
    telegram_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    telegram_linked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.full_name or self.user.get_username()

    @property
    def telegram_url(self) -> str:
        """`@nom` → `https://t.me/nom`. Bo'sh bo'lsa bo'sh satr."""
        value = (self.telegram or "").strip().lstrip("@")
        if not value:
            return ""
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://t.me/{value.removeprefix('t.me/')}"


class Restaurant(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="restaurants",
    )
    slug = models.SlugField(unique=True, max_length=60)
    name = models.CharField(max_length=120)
    #: Mijoz menyusida ko'rsatiladigan tillar, masalan `["uz", "ru", "en"]`.
    languages = models.JSONField(default=default_languages, validators=[validate_languages])
    #: Taomlar shu tilda yoziladi, qolganlari shundan tarjima qilinadi.
    primary_language = models.CharField(
        max_length=10,
        choices=[(code, name) for code, name in LANGUAGE_NAMES.items()],
        default=DEFAULT_LANGUAGE,
    )
    auto_translate = models.BooleanField(default=True)
    cuisine = models.JSONField(
        default=empty_translation, validators=[validate_translation], blank=True
    )
    address = models.JSONField(
        default=empty_translation, validators=[validate_translation], blank=True
    )
    #: Haftalik ish vaqti: `{"mon": {"open": "10:00", "close": "22:00",
    #: "closed": false}, ...}`. Vaqtlar tarjima qilinmaydi.
    working_hours = models.JSONField(
        default=default_working_hours, validators=[validate_working_hours], blank=True
    )
    phone = models.CharField(max_length=32, blank=True)
    #: Qo'shimcha raqamlar — masalan filial yoki yetkazib berish bo'limi.
    #: Asosiy raqam `phone` da qoladi: sinov muddati tekshiruvi shunga bog'liq.
    extra_phones = models.JSONField(
        default=list, validators=[validate_phone_list], blank=True
    )
    #: Bog'lanish uchun ko'rsatiladi, menyuda tarjima qilinmaydi.
    city = models.CharField(max_length=80, blank=True)
    #: Viloyat — agentlar qidiruvida "bu restoran allaqachon mijoz" deb ko'rsatish uchun.
    region = models.CharField("Viloyat", max_length=30, choices=REGION_CHOICES, blank=True)
    #: Ijtimoiy sahifalar — foydalanuvchi nomi yoki to'liq havola.
    instagram = models.CharField(max_length=120, blank=True)
    facebook = models.CharField(max_length=120, blank=True)
    telegram = models.CharField(max_length=120, blank=True)
    logo = models.ImageField(upload_to="restaurants/logos/", null=True, blank=True)
    cover = models.ImageField(upload_to="restaurants/covers/", null=True, blank=True)
    service_charge_percent = models.PositiveSmallIntegerField(default=0)
    #: QR nuqtalarining rangi — oq fonga nisbatan yetarlicha to'q (`menu/qr.py`).
    qr_color = models.CharField("QR rangi", max_length=7, default="#231c17")
    #: QR o'rtasi: vilka-pichoq belgisi (sukut), restoran logotipi yoki bo'sh.
    qr_center = models.CharField(
        "QR o'rtasi", max_length=8, choices=qr_codes.CENTER_CHOICES, default="icon"
    )
    #: Chop etiladigan kartochka: och (krem) yoki to'q (QR rangidagi) fon.
    qr_frame = models.CharField(
        "QR kartochka foni",
        max_length=8,
        choices=[("light", "Och"), ("dark", "To'q")],
        default="light",
    )
    #: Kartochkadagi yozuvlar. Bo'sh — restoran tilida standart matn
    #: ("MENYU", "Kamerani qarating") va boshqa tillardagi tarjimasi.
    qr_title = models.CharField("QR sarlavhasi", max_length=24, blank=True)
    qr_text = models.CharField("QR izohi", max_length=60, blank=True)
    qr_show_link = models.BooleanField("Kartochkada havola", default=True)
    #: Nuqta shakli va burchak kvadratlari — `menu/qr.py` dagi ro'yxatlardan.
    qr_style = models.CharField(
        "QR nuqta shakli", max_length=16, choices=qr_codes.STYLE_CHOICES, default="square"
    )
    qr_eyes = models.CharField(
        "QR burchaklari", max_length=16, choices=qr_codes.EYE_CHOICES, default="square"
    )
    is_active = models.BooleanField(default=True)
    #: O'chirilsa menyu ochiladi, lekin Google/Yandex'ga ko'rsatilmaydi
    #: (`noindex`, sitemap'da yo'q) — masalan namuna (demo) restoran uchun.
    is_listed = models.BooleanField("Qidiruvda ko'rinsin", default=True)
    #: 14 kunlik sinov bir marta beriladi — shu maydon shuni belgilaydi.
    trial_used_at = models.DateTimeField(null=True, blank=True)
    #: Qaysi savdo agenti olib kelgan (`agents` ilovasi). Birinchi to'lovdan
    #: keyin faqat Django admin'da o'zgartiriladi.
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="restaurants",
        verbose_name="Agent",
    )
    agent_attached_at = models.DateTimeField(null=True, blank=True, editable=False)
    translation_meta = models.JSONField(
        default=dict, validators=[validate_translation_meta], blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def limits(self) -> dict:
        """Joriy obunaning tarif imkoniyatlari.

        Obuna bo'lmasa (bo'lmasligi kerak — har bir restoran yaratilishda
        birga olinadi) eng cheklangan holatga qaytadi.
        """
        subscription = getattr(self, "subscription", None)
        if subscription is None:
            return {"dish_limit": 0, "stats": False}
        return subscription.plan.features

    @property
    def dish_count(self) -> int:
        return Dish.objects.filter(category__restaurant=self).count()

    def can_add_dish(self) -> bool:
        limit = self.limits["dish_limit"]
        return limit is None or self.dish_count < limit

    @property
    def secondary_languages(self) -> list[str]:
        """Asosiydan boshqa tillar — AI tarjima aynan shularga qilinadi."""
        return [code for code in self.languages if code != self.primary_language]

    @property
    def qr_url(self) -> str:
        """Butun restoran uchun bitta QR — stol tokensiz to'g'ridan-to'g'ri havola."""
        return f"{settings.SITE_URL}/{self.slug}"

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        if self.primary_language not in (self.languages or []):
            raise ValidationError(
                {"primary_language": "Asosiy til tanlangan tillar ichida bo'lishi kerak."}
            )


#: Taklif shuncha kundan keyin eskiradi.
INVITE_DAYS = 7


def generate_invite_token() -> str:
    """Taklif havolasidagi bir martalik token."""
    return secrets.token_urlsafe(24)


def invite_expiry():
    return timezone.now() + timedelta(days=INVITE_DAYS)


#: Xodimga egasi beradigan ruxsatlar. Pul, xodimlar, taomni butunlay o'chirish
#: va restoran sozlamalari bu yerda yo'q — ular doim faqat egasida.
STAFF_PERMISSIONS = {
    "stoplist": "Stop-list (taom tugadi / bor)",
    "dishes": "Taom qo'shish va tahrirlash",
    "prices": "Narxni o'zgartirish",
    "categories": "Kategoriyalar",
    "stats": "Statistikani ko'rish",
    "qr": "QR kod",
}


def all_staff_permissions() -> list[str]:
    return list(STAFF_PERMISSIONS)


class RestaurantMember(models.Model):
    """Restoranni boshqaradigan odam.

    Ilgari bitta `Restaurant.owner` bor edi — ikkinchi menejer o'z Google
    hisobi bilan kirsa alohida bo'sh restoran olardi. Endi bir restoranda
    bir nechta a'zo bo'ladi; `owner` maydoni esa kim to'laydi va kim
    restoranni o'chira oladi degan savolga javob berib qoladi.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Egasi"
        MANAGER = "manager", "Menejer"

    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MANAGER)
    #: Xodimning ruxsatlari (`STAFF_PERMISSIONS` kalitlari). Egasida hammasi bor —
    #: bu ro'yxat unga qaralmaydi.
    permissions = models.JSONField(default=all_staff_permissions, blank=True)
    #: Egasi uchun: xodim narxni o'zgartirsa yoki taom qo'shsa Telegram'ga xabar.
    notify_changes = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("restaurant", "user"), name="unique_restaurant_member"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.restaurant} ({self.role})"

    @property
    def is_owner(self) -> bool:
        return self.role == self.Role.OWNER


class RestaurantInvite(models.Model):
    """Menejerni taklif qilish.

    Pochta yuborilmaydi (loyihada pochta serveri yo'q) — egasi havolani
    o'zi yuboradi. Havolasiz ham ishlaydi: taklif qilingan pochta bilan
    Google orqali kirilganda a'zolik o'sha yerda beriladi.
    """

    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="invites"
    )
    #: Har doim kichik harfda saqlanadi — Google ham shunday qaytaradi.
    #: Telegram orqali taklifda bo'sh — xodim havolani bosib, Telegram bilan kiradi.
    email = models.EmailField(blank=True)
    name = models.CharField(max_length=80, blank=True)
    permissions = models.JSONField(default=all_staff_permissions, blank=True)
    token = models.CharField(max_length=64, unique=True, default=generate_invite_token)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=invite_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            #: Bitta restoranga bitta pochta uchun bitta kutilayotgan taklif.
            models.UniqueConstraint(
                fields=("restaurant", "email"),
                condition=models.Q(accepted_at__isnull=True) & ~models.Q(email=""),
                name="unique_pending_invite",
            )
        ]

    def __str__(self) -> str:
        return f"{self.email} → {self.restaurant}"

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.expires_at > timezone.now()

    @property
    def is_telegram(self) -> bool:
        return not self.email

    @property
    def url(self) -> str:
        """Egasi xodimga yuboradigan havola: Telegram taklifi — botga, qolgani — saytga."""
        if self.is_telegram:
            return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=I{self.token}"
        return f"{settings.SITE_URL}/join/{self.token}"


LINK_MINUTES = 10


def link_expiry():
    return timezone.now() + timedelta(minutes=LINK_MINUTES)


class TelegramLinkToken(models.Model):
    """Admin paneldan "Telegram'ni ulash" — bir martalik, 10 daqiqalik kalit."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_links")
    token = models.CharField(max_length=64, unique=True, default=generate_invite_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=link_expiry)
    used_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    @property
    def url(self) -> str:
        return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=L{self.token}"


class ActivityLog(models.Model):
    """"Kim nima qildi" — menyudagi o'zgarishlar (ko'rishlar emas). 90 kun saqlanadi."""

    class Action(models.TextChoices):
        DISH_CREATED = "dish_created", "Taom qo'shildi"
        DISH_UPDATED = "dish_updated", "Taom tahrirlandi"
        PRICE_CHANGED = "price_changed", "Narx o'zgardi"
        STOCK_OFF = "stock_off", "Tugadi"
        STOCK_ON = "stock_on", "Yana bor"
        DISH_DELETED = "dish_deleted", "Taom o'chirildi"
        CATEGORY_CREATED = "category_created", "Kategoriya qo'shildi"
        CATEGORY_UPDATED = "category_updated", "Kategoriya tahrirlandi"
        CATEGORY_DELETED = "category_deleted", "Kategoriya o'chirildi"

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="activity")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: Ism saqlab qo'yiladi — xodim chiqarilgandan keyin ham jurnalda ko'rinsin.
    actor = models.CharField(max_length=120)
    action = models.CharField(max_length=20, choices=Action.choices)
    target = models.CharField(max_length=160)
    detail = models.CharField(max_length=200, blank=True)
    #: Qaytarish uchun: `{"dish": id, "old": 45000, "new": 50000}`.
    data = models.JSONField(default=dict, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "o'zgarish"
        verbose_name_plural = "o'zgarishlar jurnali"

    def __str__(self) -> str:
        return f"{self.actor}: {self.get_action_display()} — {self.target}"


class Plan(models.Model):
    """Tarif — narx va imkoniyatlar. Ma'lumotlar migratsiya bilan yaratiladi."""

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=40)
    price_month = models.PositiveIntegerField(help_text="so'm")
    price_half_year = models.PositiveIntegerField(default=0, help_text="so'm, 6 oy uchun")
    price_year = models.PositiveIntegerField(help_text="so'm")
    #: Masalan `{"dish_limit": None, "stats": True}` — `None` = cheksiz.
    features = models.JSONField(default=dict, blank=True)
    #: Narxlar sahifasida ko'rsatiladimi (Pro hozircha "tez orada").
    is_public = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name

    def price_for(self, period: str) -> int:
        """Tanlangan davr uchun to'lanadigan summa — narx faqat shu yerdan olinadi."""
        return {
            Subscription.Period.MONTH: self.price_month,
            Subscription.Period.HALF_YEAR: self.price_half_year,
            Subscription.Period.YEAR: self.price_year,
        }[period]


class Subscription(models.Model):
    """Restoranning obunasi — bitta restoranga bitta obuna."""

    class Status(models.TextChoices):
        TRIALING = "trialing", "Sinov"
        ACTIVE = "active", "Faol"
        PAST_DUE = "past_due", "To'lanmadi"
        SUSPENDED = "suspended", "To'xtatilgan"
        CANCELED = "canceled", "Bekor qilingan"

    class Period(models.TextChoices):
        MONTH = "month", "Oylik"
        HALF_YEAR = "half", "6 oylik"
        YEAR = "year", "Yillik"

    restaurant = models.OneToOneField(
        Restaurant, on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices)
    period = models.CharField(max_length=8, choices=Period.choices, default=Period.MONTH)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    #: Joriy davr uchun to'langan bo'lsa, keyingi yechish shu sanada.
    current_period_end = models.DateTimeField(null=True, blank=True)
    #: `past_due` bo'lgach, shu sanagacha menyu ishlayveradi.
    grace_ends_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.restaurant.slug} · {self.status}"

    @property
    def is_menu_active(self) -> bool:
        """Faqat `suspended` holatida mijoz menyusi to'xtaydi."""
        return self.status != Subscription.Status.SUSPENDED

    @property
    def price(self) -> int:
        """Joriy davr uchun to'lanadigan summa."""
        return self.plan.price_for(self.period)


class PaymentReceipt(models.Model):
    """Restoran egasi yuklagan to'lov cheki (skrinshot).

    To'lov qo'lda: egasi platforma kartasiga pul o'tkazadi, chekni yuklaydi,
    chek Telegram bot orqali platforma egasiga boradi va u tasdiqlaydi yoki
    rad etadi. Faqat tasdiqlangan chek obunani uzaytiradi.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Tekshirilmoqda"
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Rad etilgan"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="receipts"
    )
    image = models.ImageField(upload_to="receipts/")
    #: Serverda tarif narxidan hisoblanadi — mijoz yuborgan qiymat emas.
    amount = models.PositiveIntegerField(help_text="so'm")
    period = models.CharField(max_length=8, choices=Subscription.Period.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    #: Rad etilgan bo'lsa — sababi.
    note = models.CharField(max_length=200, blank=True)
    telegram_message_id = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.subscription.restaurant.slug} · {self.amount} so'm · {self.status}"


class Invoice(models.Model):
    """Har bir yechilgan (yoki muvaffaqiyatsiz) to'lov."""

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        PAID = "paid", "To'landi"
        FAILED = "failed", "Amalga oshmadi"
        REFUNDED = "refunded", "Qaytarildi"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="invoices"
    )
    amount = models.PositiveIntegerField(help_text="so'm")
    period = models.CharField(max_length=8, choices=Subscription.Period.choices)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices)
    #: Bir xil to'lov ikki marta yozilmasligi uchun (`receipt_<id>`).
    provider_payment_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    #: Shu hisob-faktura qaysi chekdan yaratilgan — egasi keyin ham ko'ra oladi.
    receipt = models.ForeignKey(
        PaymentReceipt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.subscription.restaurant.slug} · {self.amount} so'm · {self.status}"


class ClickPayment(models.Model):
    """Click orqali to'lov urinishi (SHOP API: Prepare → Complete).

    Egasi davrni tanlaydi — summa serverda tarifdan hisoblanadi va shu yerda
    qotiriladi. Click'ga `merchant_trans_id` sifatida shu qatorning `pk` i
    boradi. Complete muvaffaqiyatli bo'lsa `billing_ledger.record_payment`
    obunani uzaytiradi (`Invoice.provider_payment_id = click_<click_trans_id>`).
    """

    class Status(models.TextChoices):
        CREATED = "created", "Yaratildi"
        PREPARED = "prepared", "Click tekshirdi"
        PAID = "paid", "To'landi"
        CANCELED = "canceled", "Bekor qilindi"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="click_payments"
    )
    #: So'mda, butun son — tarif narxidan, mijozdan emas.
    amount = models.PositiveIntegerField(help_text="so'm")
    period = models.CharField(max_length=8, choices=Subscription.Period.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    click_trans_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    click_paydoc_id = models.BigIntegerField(null=True, blank=True)
    #: Bekor qilinganda Click yuborgan xato (masalan "Insufficient funds").
    error_note = models.CharField(max_length=200, blank=True)
    invoice = models.OneToOneField(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="click_payment"
    )
    #: Soliq cheki (OFD) Click'ga yuborilgan vaqt; xato bo'lsa — `fiscal_error`.
    fiscalized_at = models.DateTimeField(null=True, blank=True)
    fiscal_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Click #{self.pk} · {self.subscription.restaurant.slug} · {self.amount} so'm · {self.status}"


class Category(models.Model):
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.JSONField(default=empty_translation, validators=[validate_translation])
    subtitle = models.JSONField(
        default=empty_translation, validators=[validate_translation], blank=True
    )
    #: Lucide ikonkasining nomi, masalan `cooking-pot`.
    icon = models.CharField(max_length=40, default="utensils-crossed")
    photo = models.ImageField(upload_to="categories/", null=True, blank=True)
    #: Kesilmagan asl nusxa — mijoz rasmni bosganda to'liq holida ko'radi.
    photo_original = models.ImageField(
        upload_to="categories/originals/", null=True, blank=True
    )
    position = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    #: "Faqat belgilangan vaqtda" — masalan nonushta 08:00–11:30.
    visible_from = models.TimeField(null=True, blank=True)
    visible_to = models.TimeField(null=True, blank=True)
    translation_meta = models.JSONField(
        default=dict, validators=[validate_translation_meta], blank=True
    )

    class Meta:
        ordering = ("position", "id")
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return f"{self.restaurant.slug} · {self.name.get(DEFAULT_LANGUAGE, '')}"

    def is_open_at(self, moment) -> bool:
        """Kategoriya shu vaqtda mijozga ko'rinadimi.

        Vaqt oralig'i tunni kesib o'tishi mumkin (masalan 22:00–02:00).
        """
        if not self.is_visible:
            return False
        if self.visible_from is None or self.visible_to is None:
            return True

        now = moment.time()
        if self.visible_from <= self.visible_to:
            return self.visible_from <= now <= self.visible_to
        return now >= self.visible_from or now <= self.visible_to


class Dish(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="dishes"
    )
    name = models.JSONField(default=empty_translation, validators=[validate_translation])
    description = models.JSONField(
        default=empty_translation, validators=[validate_translation], blank=True
    )
    ingredients = models.JSONField(
        default=empty_translation_list,
        validators=[validate_translation_list],
        blank=True,
    )
    #: Narx — butun son, so'mda.
    price = models.PositiveIntegerField()
    weight = models.PositiveIntegerField(null=True, blank=True)
    unit = models.CharField(max_length=4, choices=Unit.choices, default=Unit.GRAM)
    kcal = models.PositiveIntegerField(null=True, blank=True)
    badges = models.JSONField(default=list, blank=True)
    is_available = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    translation_meta = models.JSONField(
        default=dict, validators=[validate_translation_meta], blank=True
    )

    class Meta:
        ordering = ("position", "id")
        verbose_name_plural = "dishes"

    def __str__(self) -> str:
        return self.name.get(DEFAULT_LANGUAGE, "") or f"Dish {self.pk}"

    @property
    def photo(self):
        """Asosiy rasm — `DishPhoto` lardagi birinchisi."""
        first = self.photos.first()
        return first.image if first else None

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        if not isinstance(self.badges, list):
            raise ValidationError({"badges": "Belgilar ro'yxat bo'lishi kerak."})
        unknown = set(self.badges) - set(Badge.values)
        if unknown:
            raise ValidationError(
                {"badges": f"Noma'lum belgilar: {', '.join(sorted(unknown))}."}
            )


class DishPhoto(models.Model):
    """Taom rasmlari. Birinchisi (eng kichik `position`) — asosiy rasm."""

    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="photos")
    #: Menyuda ko'rsatiladigan, egasi kesgan nusxa.
    image = models.ImageField(upload_to="dishes/")
    #: Kesilmagan asl nusxa — mijoz rasmni bosganda to'liq holida ko'radi.
    original = models.ImageField(upload_to="dishes/originals/", null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")

    def __str__(self) -> str:
        return f"{self.dish} · {self.position}"


class MenuView(models.Model):
    """Ko'rish hodisasi — statistika shundan hisoblanadi. Daromad ma'lumoti yo'q."""

    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="views"
    )
    dish = models.ForeignKey(
        Dish, on_delete=models.SET_NULL, null=True, blank=True, related_name="views"
    )
    kind = models.CharField(max_length=16, choices=ViewKind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("restaurant", "created_at")),
            models.Index(fields=("restaurant", "kind", "created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.restaurant.slug} · {self.kind} · {self.created_at:%Y-%m-%d %H:%M}"
