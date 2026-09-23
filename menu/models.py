"""stolda.uz ma'lumotlar modeli.

Menyu faqat ko'rsatish uchun: buyurtma, savat va to'lov yo'q.
Statistika ham faqat ko'rishlardan iborat (MenuView).
"""

import secrets

from django.conf import settings
from django.db import models

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


def generate_qr_token() -> str:
    return secrets.token_urlsafe(8)


class Unit(models.TextChoices):
    GRAM = "g", "g"
    MILLILITER = "ml", "ml"


class Badge(models.TextChoices):
    POPULAR = "popular", "Mashhur"
    VEG = "veg", "Vegetarian"


class ViewKind(models.TextChoices):
    SCAN = "scan", "QR skaner"
    DISH_OPEN = "dish_open", "Taom ochildi"


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
    hours = models.JSONField(
        default=empty_translation, validators=[validate_translation], blank=True
    )
    phone = models.CharField(max_length=32, blank=True)
    #: Bog'lanish uchun ko'rsatiladi, menyuda tarjima qilinmaydi.
    city = models.CharField(max_length=80, blank=True)
    instagram = models.CharField(max_length=64, blank=True)
    logo = models.ImageField(upload_to="restaurants/logos/", null=True, blank=True)
    cover = models.ImageField(upload_to="restaurants/covers/", null=True, blank=True)
    service_charge_percent = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    #: 14 kunlik sinov bir marta beriladi — shu maydon shuni belgilaydi.
    trial_used_at = models.DateTimeField(null=True, blank=True)
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
        return f"https://stolda.uz/{self.slug}"

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        if self.primary_language not in (self.languages or []):
            raise ValidationError(
                {"primary_language": "Asosiy til tanlangan tillar ichida bo'lishi kerak."}
            )


class Plan(models.Model):
    """Tarif — narx va imkoniyatlar. Ma'lumotlar migratsiya bilan yaratiladi."""

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=40)
    price_month = models.PositiveIntegerField(help_text="so'm")
    price_year = models.PositiveIntegerField(help_text="so'm")
    #: Masalan `{"dish_limit": None, "stats": True}` — `None` = cheksiz.
    features = models.JSONField(default=dict, blank=True)
    #: Narxlar sahifasida ko'rsatiladimi (Pro hozircha "tez orada").
    is_public = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


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
    autopay = models.BooleanField(default=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.restaurant.slug} · {self.status}"

    @property
    def is_menu_active(self) -> bool:
        """Faqat `suspended` holatida mijoz menyusi to'xtaydi."""
        return self.status != Subscription.Status.SUSPENDED


class PaymentMethod(models.Model):
    """Saqlangan karta — takroriy to'lov uchun faqat token, karta raqami emas."""

    class Provider(models.TextChoices):
        PAYME = "payme", "Payme"
        CLICK = "click", "Click"

    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="payment_methods"
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    #: Provayderning saqlangan karta tokeni — karta raqamining o'zi emas.
    token = models.CharField(max_length=128)
    brand = models.CharField(max_length=16, blank=True)
    last4 = models.CharField(max_length=4)
    exp_month = models.PositiveSmallIntegerField()
    exp_year = models.PositiveSmallIntegerField()
    is_default = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-is_default", "-created_at")

    def __str__(self) -> str:
        return f"{self.get_provider_display()} •••• {self.last4}"


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
    #: Webhook'lar idempotent bo'lishi uchun — bir xil to'lov ikki marta yozilmaydi.
    provider_payment_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    #: OFD fiskal chek havolasi — provayder qaytargan bo'lsa.
    receipt_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.subscription.restaurant.slug} · {self.amount} so'm · {self.status}"


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
    image = models.ImageField(upload_to="dishes/")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")

    def __str__(self) -> str:
        return f"{self.dish} · {self.position}"


class Table(models.Model):
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="tables"
    )
    number = models.CharField(max_length=16)
    qr_token = models.CharField(
        max_length=32, unique=True, default=generate_qr_token, editable=False
    )

    class Meta:
        ordering = ("restaurant", "number")
        constraints = [
            models.UniqueConstraint(
                fields=("restaurant", "number"), name="unique_table_number_per_restaurant"
            )
        ]

    def __str__(self) -> str:
        return f"{self.restaurant.slug} · {self.number}-stol"

    @property
    def qr_url(self) -> str:
        return f"https://stolda.uz/{self.restaurant.slug}?t={self.qr_token}"


class MenuView(models.Model):
    """Ko'rish hodisasi — statistika shundan hisoblanadi. Daromad ma'lumoti yo'q."""

    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="views"
    )
    dish = models.ForeignKey(
        Dish, on_delete=models.SET_NULL, null=True, blank=True, related_name="views"
    )
    table = models.ForeignKey(
        Table, on_delete=models.SET_NULL, null=True, blank=True, related_name="views"
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
