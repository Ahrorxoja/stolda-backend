from django.contrib import admin

from .models import (
    Category,
    Dish,
    Invoice,
    MenuView,
    PaymentMethod,
    Plan,
    Restaurant,
    Subscription,
    Table,
)
from .translations import translate


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "subscription_status", "is_active", "dish_count")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "phone")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="obuna")
    def subscription_status(self, obj: Restaurant) -> str:
        subscription = getattr(obj, "subscription", None)
        return subscription.get_status_display() if subscription else "—"


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_month", "price_year", "is_public")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("restaurant", "plan", "status", "current_period_end", "grace_ends_at")
    list_filter = ("status", "plan")
    search_fields = ("restaurant__name", "restaurant__slug")


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("restaurant", "provider", "brand", "last4", "is_default")
    list_filter = ("provider",)


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


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("number", "restaurant", "qr_token", "qr_url")
    list_filter = ("restaurant",)
    readonly_fields = ("qr_token",)


@admin.register(MenuView)
class MenuViewAdmin(admin.ModelAdmin):
    list_display = ("created_at", "restaurant", "kind", "dish", "table")
    list_filter = ("kind", "restaurant", "created_at")
    date_hierarchy = "created_at"
