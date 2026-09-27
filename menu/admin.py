from django.contrib import admin

from .models import (
    Category,
    Dish,
    Invoice,
    MenuView,
    PaymentReceipt,
    Plan,
    PlatformSettings,
    Profile,
    Restaurant,
    Subscription,
)
from .translations import translate


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "subscription_status",
        "owner_contact",
        "owner_telegram",
        "is_active",
        "dish_count",
    )
    list_filter = ("is_active",)
    search_fields = (
        "name",
        "slug",
        "phone",
        "owner__username",
        "owner__profile__contact_phone",
        "owner__profile__telegram",
    )
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner__profile", "subscription")

    @admin.display(description="obuna")
    def subscription_status(self, obj: Restaurant) -> str:
        subscription = getattr(obj, "subscription", None)
        return subscription.get_status_display() if subscription else "—"

    @admin.display(description="egasi bilan aloqa")
    def owner_contact(self, obj: Restaurant) -> str:
        profile = getattr(obj.owner, "profile", None)
        phone = (profile.contact_phone if profile else "") or obj.phone
        name = (profile.full_name if profile else "") or obj.owner.get_username()
        return f"{name} · {phone}" if phone else name

    @admin.display(description="telegram")
    def owner_telegram(self, obj: Restaurant) -> str:
        profile = getattr(obj.owner, "profile", None)
        return (profile.telegram if profile else "") or "—"


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """Hisob egalarining aloqa ma'lumotlari — to'lov kechikkanda kerak bo'ladi."""

    list_display = ("user", "full_name", "contact_phone", "telegram", "updated_at")
    search_fields = ("user__username", "full_name", "contact_phone", "telegram")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_month", "price_year", "is_public")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("restaurant", "plan", "status", "current_period_end", "grace_ends_at")
    list_filter = ("status", "plan")
    search_fields = ("restaurant__name", "restaurant__slug")


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("subscription", "amount", "period", "status", "created_at")
    list_filter = ("status", "period")
    date_hierarchy = "created_at"


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("subscription", "amount", "status", "paid_at", "provider_payment_id")
    list_filter = ("status",)
    date_hierarchy = "created_at"


class DishInline(admin.TabularInline):
    model = Dish
    extra = 0
    fields = ("name", "price", "is_available", "position")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("uz_name", "restaurant", "icon", "position")
    list_filter = ("restaurant",)
    ordering = ("restaurant", "position")
    inlines = (DishInline,)

    @admin.display(description="nomi")
    def uz_name(self, obj: Category) -> str:
        return translate(obj.name)


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("uz_name", "category", "price", "is_available", "position")
    list_filter = ("is_available", "category__restaurant", "category")
    search_fields = ("name",)
    ordering = ("category", "position")

    @admin.display(description="nomi")
    def uz_name(self, obj: Dish) -> str:
        return translate(obj.name)


@admin.register(MenuView)
class MenuViewAdmin(admin.ModelAdmin):
    list_display = ("created_at", "restaurant", "kind", "dish")
    list_filter = ("kind", "restaurant", "created_at")
    date_hierarchy = "created_at"


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    """Bitta yozuv: karta va yordam aloqalari. Qo'shish/o'chirish yopiq."""

    list_display = ("card_number", "card_holder", "support_phone", "updated_at")
    fieldsets = (
        ("To'lov kartasi", {
            "fields": ("card_number", "card_holder"),
            "description": "Restoran egalari obuna uchun shu kartaga pul o'tkazadi.",
        }),
        ("Yordam aloqalari", {
            "fields": ("support_phone", "support_telegram"),
            "description": "To'lov sahifasida ko'rinadi — muammo chiqqanda murojaat uchun.",
        }),
    )

    def has_add_permission(self, request) -> bool:
        return not PlatformSettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        """Ro'yxat o'rniga to'g'ridan-to'g'ri yagona yozuvni ochadi."""
        from django.shortcuts import redirect
        from django.urls import reverse

        obj = PlatformSettings.load()
        return redirect(reverse("admin:menu_platformsettings_change", args=[obj.pk]))
