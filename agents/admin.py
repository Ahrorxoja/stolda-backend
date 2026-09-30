from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils.html import format_html

from . import services
from . import journal
from .models import Agent, AgentEarning, AgentWithdrawal, Place, Visit


class ApplicationFilter(admin.SimpleListFilter):
    title = "ariza"
    parameter_name = "ariza"

    def lookups(self, request, model_admin):
        return (("pending", "⏳ Kutilmoqda"), ("rejected", "Rad etilgan"))

    def queryset(self, request, queryset):
        if self.value() == "pending":
            return queryset.filter(applied_at__isnull=False, approved_at__isnull=True, rejected_at__isnull=True)
        if self.value() == "rejected":
            return queryset.filter(rejected_at__isnull=False)
        return queryset


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "state",
        "telegram",
        "restaurants_count",
        "paying_count",
        "available",
        "paid_total",
        "is_active",
    )
    list_filter = ("is_active", ApplicationFilter)
    search_fields = ("name", "code", "phone", "telegram_username", "city")
    readonly_fields = (
        "links",
        "telegram",
        "balance_info",
        "created_at",
        "applied_at",
        "approved_at",
        "rejected_at",
        "rules_version",
        "rules_accepted_at",
    )
    actions = ("approve", "reject")
    fieldsets = (
        (None, {"fields": ("name", "phone", "city", "code", "is_active")}),
        ("Ariza", {"fields": ("note", "applied_at", "approved_at", "rejected_at")}),
        ("Qoidalar", {"fields": ("rules_version", "rules_accepted_at")}),
        ("Havolalar", {"fields": ("links", "telegram")}),
        ("Shartlar", {"fields": ("first_percent", "percent", "months")}),
        ("Karta", {"fields": ("card_number", "card_holder")}),
        ("Balans", {"fields": ("balance_info", "created_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _restaurants=Count("restaurants", distinct=True),
                _paying=Count(
                    "restaurants",
                    filter=Q(restaurants__subscription__status="active"),
                    distinct=True,
                ),
            )
        )

    @admin.display(description="holati")
    def state(self, obj: Agent) -> str:
        if obj.is_pending:
            return "⏳ ariza"
        if obj.rejected_at:
            return "rad etilgan"
        return "faol" if obj.is_active else "o'chirilgan"

    @admin.action(description="✅ Arizani qabul qilish (kod beriladi)")
    def approve(self, request, queryset):
        done = sum(services.approve_application(agent) for agent in queryset)
        self.message_user(request, f"{done} ta ariza qabul qilindi.", messages.SUCCESS)

    @admin.action(description="❌ Arizani rad etish")
    def reject(self, request, queryset):
        done = sum(services.reject_application(agent) for agent in queryset)
        self.message_user(request, f"{done} ta ariza rad etildi.", messages.WARNING)

    @admin.display(description="restoranlar", ordering="_restaurants")
    def restaurants_count(self, obj: Agent) -> int:
        return obj._restaurants

    @admin.display(description="to'layapti", ordering="_paying")
    def paying_count(self, obj: Agent) -> int:
        return obj._paying

    @admin.display(description="balans")
    def available(self, obj: Agent) -> str:
        return services.money(services.balance(obj).available)

    @admin.display(description="to'langan")
    def paid_total(self, obj: Agent) -> str:
        return services.money(services.balance(obj).paid)

    @admin.display(description="Telegram")
    def telegram(self, obj: Agent) -> str:
        if not obj.telegram_chat_id:
            return "— ulanmagan"
        return f"@{obj.telegram_username}" if obj.telegram_username else "✓ ulangan"

    @admin.display(description="Havolalar")
    def links(self, obj: Agent):
        if not obj.pk:
            return "Saqlangandan keyin paydo bo'ladi."
        invite = services.invite_link(obj)
        invite_html = (
            format_html(
                "<b>Botga taklif</b> (agentga yuboring):<br><code>{}</code>", invite
            )
            if invite
            else "Botga taklif: AGENT_BOT_USERNAME sozlanmagan."
        )
        return format_html(
            "{}<br><br><b>Restoranlar uchun havola</b>:<br><code>{}</code>",
            invite_html,
            services.agent_link(obj),
        )

    @admin.display(description="Balans")
    def balance_info(self, obj: Agent) -> str:
        if not obj.pk:
            return "—"
        b = services.balance(obj)
        return (
            f"Yechish mumkin: {services.money(b.available)} · "
            f"Ko'rib chiqilmoqda: {services.money(b.pending)} · "
            f"O'tkazilgan: {services.money(b.paid)} · "
            f"Shu oyda: {services.money(b.this_month)}"
        )


@admin.register(AgentEarning)
class AgentEarningAdmin(admin.ModelAdmin):
    list_display = ("created_at", "agent", "restaurant_name", "kind", "amount", "withdrawal_status")
    list_filter = ("agent", "kind", ("created_at", admin.DateFieldListFilter))
    search_fields = ("restaurant_name", "agent__name", "agent__code")
    readonly_fields = ("agent", "restaurant", "restaurant_name", "invoice", "kind", "amount", "withdrawal", "created_at")

    def has_add_permission(self, request) -> bool:
        return False  # Faqat to'lov tasdiqlanganda avtomatik yoziladi.

    @admin.display(description="holati")
    def withdrawal_status(self, obj: AgentEarning) -> str:
        if obj.withdrawal is None:
            return "balansda"
        return obj.withdrawal.get_status_display()


@admin.register(AgentWithdrawal)
class AgentWithdrawalAdmin(admin.ModelAdmin):
    list_display = ("created_at", "agent", "amount", "card_number", "card_holder", "status", "processed_at")
    list_filter = ("status", "agent")
    readonly_fields = (
        "agent",
        "amount",
        "card_number",
        "card_holder",
        "status",
        "note",
        "telegram_message_id",
        "created_at",
        "processed_at",
    )
    actions = ("mark_paid", "mark_rejected")

    def has_add_permission(self, request) -> bool:
        return False  # Agent botdan so'raydi.

    @admin.action(description="✅ O'tkazildi deb belgilash")
    def mark_paid(self, request, queryset):
        done = sum(services.settle_withdrawal(item, paid=True) for item in queryset)
        self.message_user(request, f"{done} ta so'rov o'tkazildi deb belgilandi.", messages.SUCCESS)

    @admin.action(description="❌ Rad etish (pul balansga qaytadi)")
    def mark_rejected(self, request, queryset):
        done = sum(services.settle_withdrawal(item, paid=False) for item in queryset)
        self.message_user(request, f"{done} ta so'rov rad etildi.", messages.WARNING)


class VisitInline(admin.TabularInline):
    model = Visit
    extra = 0
    fields = ("created_at", "agent", "outcome", "comment")
    readonly_fields = ("created_at", "agent", "outcome")
    ordering = ("-created_at",)


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    """Agentlar borgan joylar — noto'g'ri yozuvni tuzatish yoki o'chirish shu yerda."""

    list_display = ("name", "address", "region", "last_outcome", "visits_count", "reserved", "restaurant")
    list_filter = ("region",)
    search_fields = ("name", "address")
    readonly_fields = ("created_by", "created_at")
    raw_id_fields = ("restaurant",)
    inlines = (VisitInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_visits=Count("visits")).prefetch_related("visits__agent")

    @admin.display(description="oxirgi natija")
    def last_outcome(self, obj: Place) -> str:
        last = obj.visits.all()[:1]
        return last[0].get_outcome_display() if last else "—"

    @admin.display(description="tashriflar", ordering="_visits")
    def visits_count(self, obj: Place) -> int:
        return obj._visits

    @admin.display(description="band")
    def reserved(self, obj: Place) -> str:
        held = journal.reservation(obj)
        return f"🟡 {journal.first_name(held.agent)} ({held.days_left} kun)" if held else ""


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("created_at", "place", "agent", "outcome", "comment")
    list_filter = ("outcome", "place__region", "agent")
    search_fields = ("place__name", "comment", "agent__name")
    raw_id_fields = ("place",)

