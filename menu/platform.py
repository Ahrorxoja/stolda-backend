"""Platforma egasi uchun umumiy raqamlar: pul, restoranlar, agentlar.

Faqat haqiqiy yozuvlardan: tasdiqlangan hisob-fakturalar (`Invoice`),
obunalar, agent daromadlari va pul yechishlar. Admin panelning
"Platforma" sahifasi va oylik Telegram hisoboti shu yerdan oladi.
"""

from datetime import datetime

from django.db.models import Count, Min, Q, Sum
from django.utils import timezone

from agents.models import Agent, AgentEarning, AgentWithdrawal, Visit

from .models import Invoice, PaymentReceipt, Restaurant, RestaurantMember, Subscription

SERIES_MONTHS = 12


def month_start(year: int, month: int) -> datetime:
    return timezone.make_aware(datetime(year, month, 1))


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_range(year: int, month: int) -> tuple[datetime, datetime]:
    return month_start(year, month), month_start(*shift_month(year, month, 1))


def _sum(queryset, field: str = "amount") -> int:
    return queryset.aggregate(total=Sum(field))["total"] or 0


def monthly_revenue(start, end) -> int:
    return _sum(Invoice.objects.filter(status=Invoice.Status.PAID, paid_at__gte=start, paid_at__lt=end))


def monthly_agent_share(start, end) -> int:
    return _sum(AgentEarning.objects.filter(created_at__gte=start, created_at__lt=end))


def mrr() -> int:
    """Hozir to'layotgan restoranlar oyiga qancha beradi (6 oylik — 6 ga, yillik — 12 ga bo'lib)."""
    months = {
        Subscription.Period.MONTH: 1,
        Subscription.Period.HALF_YEAR: 6,
        Subscription.Period.YEAR: 12,
    }
    total = 0
    for subscription in Subscription.objects.filter(status=Subscription.Status.ACTIVE).select_related("plan"):
        total += subscription.price // months[subscription.period]
    return total


def overview(year: int, month: int) -> dict:
    start, end = month_range(year, month)
    now = timezone.now()

    revenue = monthly_revenue(start, end)
    agent_share = monthly_agent_share(start, end)

    series = []
    for offset in range(SERIES_MONTHS - 1, -1, -1):
        y, m = shift_month(year, month, -offset)
        s, e = month_range(y, m)
        series.append({"month": f"{y:04d}-{m:02d}", "revenue": monthly_revenue(s, e), "agents": monthly_agent_share(s, e)})

    # ── Restoranlar ──
    statuses = dict(
        Subscription.objects.values_list("status").annotate(n=Count("pk")).values_list("status", "n")
    )
    first_paid = Invoice.objects.filter(status=Invoice.Status.PAID).values("subscription").annotate(first=Min("paid_at"))
    converted_month = first_paid.filter(first__gte=start, first__lt=end).count()
    # Konversiya: sinov muddati tugagan restoranlardan necha foizi kamida bir marta to'lagan.
    trial_over = Subscription.objects.filter(trial_ends_at__lt=now)
    ever_paid = set(first_paid.values_list("subscription", flat=True))
    trial_over_ids = set(trial_over.values_list("pk", flat=True))
    conversion = round(len(trial_over_ids & ever_paid) / len(trial_over_ids) * 100) if trial_over_ids else None

    regions: dict[str, dict] = {}
    for region, status in Restaurant.objects.filter(subscription__isnull=False).values_list(
        "region", "subscription__status"
    ):
        row = regions.setdefault(region or "", {"total": 0, "paying": 0})
        row["total"] += 1
        row["paying"] += status == Subscription.Status.ACTIVE

    # ── Agentlar ──
    agents = []
    for agent in Agent.objects.exclude(code__isnull=True).order_by("name"):
        earnings = AgentEarning.objects.filter(agent=agent)
        restaurants = agent.restaurants.all()
        agents.append(
            {
                "id": agent.pk,
                "name": agent.name,
                "code": agent.code,
                "is_active": agent.is_active,
                "visits": Visit.objects.filter(agent=agent).count(),
                "visits_month": Visit.objects.filter(agent=agent, created_at__gte=start, created_at__lt=end).count(),
                "restaurants": restaurants.count(),
                "paying": restaurants.filter(subscription__status=Subscription.Status.ACTIVE).count(),
                "earned_month": _sum(earnings.filter(created_at__gte=start, created_at__lt=end)),
                "balance": _sum(earnings.filter(withdrawal__isnull=True)),
            }
        )
    agents.sort(key=lambda row: (row["earned_month"], row["paying"], row["visits_month"]), reverse=True)

    telegram = telegram_status()
    pending_withdrawals = AgentWithdrawal.objects.filter(status=AgentWithdrawal.Status.PENDING)
    return {
        "month": f"{year:04d}-{month:02d}",
        "money": {
            "revenue": revenue,
            "payments": Invoice.objects.filter(status=Invoice.Status.PAID, paid_at__gte=start, paid_at__lt=end).count(),
            "mrr": mrr(),
            "agent_share": agent_share,
            "agent_paid": _sum(
                AgentWithdrawal.objects.filter(
                    status=AgentWithdrawal.Status.PAID, processed_at__gte=start, processed_at__lt=end
                )
            ),
            # Agentlarga hali o'tkazilmagan hamma pul (balans + kutilayotgan so'rovlar).
            "agent_owed": _sum(
                AgentEarning.objects.filter(
                    Q(withdrawal__isnull=True) | Q(withdrawal__status=AgentWithdrawal.Status.PENDING)
                )
            ),
            "net": revenue - agent_share,
        },
        "series": series,
        "restaurants": {
            "trialing": statuses.get(Subscription.Status.TRIALING, 0),
            "active": statuses.get(Subscription.Status.ACTIVE, 0),
            "past_due": statuses.get(Subscription.Status.PAST_DUE, 0),
            "suspended": statuses.get(Subscription.Status.SUSPENDED, 0),
            "new": Restaurant.objects.filter(created_at__gte=start, created_at__lt=end).count(),
            "converted": converted_month,
            "conversion": conversion,
        },
        "regions": sorted(
            ({"region": name, **row} for name, row in regions.items()),
            key=lambda row: (-row["total"], row["region"]),
        ),
        "attention": {
            "applications": Agent.objects.filter(
                applied_at__isnull=False, approved_at__isnull=True, rejected_at__isnull=True
            ).count(),
            "withdrawals": pending_withdrawals.count(),
            "withdrawals_sum": _sum(pending_withdrawals),
            "receipts": PaymentReceipt.objects.filter(status=PaymentReceipt.Status.PENDING).count(),
            "past_due": statuses.get(Subscription.Status.PAST_DUE, 0),
            "telegram": len(telegram["unlinked"]),
        },
        "telegram": telegram,
        "agents": agents,
    }


#: Hali ishlayotgan restoranlar — to'xtatilgan va bekor qilinganlar hisobga kirmaydi.
LIVE_STATUSES = (Subscription.Status.TRIALING, Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE)


def unlinked_restaurants():
    """Egasi Telegram'ini (@Stoldabot) ulamagan, hali ishlayotgan restoranlar."""
    linked_owners = RestaurantMember.objects.filter(
        role=RestaurantMember.Role.OWNER, user__profile__telegram_id__isnull=False
    ).values("restaurant")
    return (
        Restaurant.objects.filter(subscription__status__in=LIVE_STATUSES)
        .exclude(pk__in=linked_owners)
        .select_related("owner", "owner__profile", "agent")
        .order_by("-created_at")
    )


def telegram_status() -> dict:
    total = Restaurant.objects.filter(subscription__status__in=LIVE_STATUSES).count()
    unlinked = list(unlinked_restaurants())
    return {
        "total": total,
        "linked": total - len(unlinked),
        "unlinked": [
            {
                "name": restaurant.name,
                "slug": restaurant.slug,
                "owner": restaurant.owner.get_username() if restaurant.owner else "",
                "phone": restaurant.phone
                or (getattr(getattr(restaurant.owner, "profile", None), "contact_phone", "") or ""),
                "agent": restaurant.agent.name if restaurant.agent_id else "",
            }
            for restaurant in unlinked[:100]
        ],
    }


def money_text(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def monthly_report_text(year: int, month: int) -> str:
    """Oylik Telegram hisoboti — o'tgan oy uchun."""
    data = overview(year, month)
    money, restaurants = data["money"], data["restaurants"]
    top = [row for row in data["agents"] if row["earned_month"]][:3]
    lines = [
        f"📊 <b>{data['month']} — oylik hisobot</b>\n",
        f"💰 Tushdi: <b>{money_text(money['revenue'])} so'm</b> ({money['payments']} ta to'lov)",
        f"🤝 Agentlar ulushi: {money_text(money['agent_share'])} so'm",
        f"✅ Sof: <b>{money_text(money['net'])} so'm</b>",
        f"🔁 Oylik barqaror daromad (MRR): {money_text(money['mrr'])} so'm\n",
        f"🍽 To'layotgan: {restaurants['active']} · Sinovda: {restaurants['trialing']} · "
        f"Qarzdor: {restaurants['past_due']} · To'xtatilgan: {restaurants['suspended']}",
        f"🆕 Yangi restoran: {restaurants['new']} · To'lovga o'tgan: {restaurants['converted']}",
    ]
    if top:
        lines.append("\n🏆 Eng yaxshi agentlar:")
        lines += [f"• {row['name']} — {money_text(row['earned_month'])} so'm" for row in top]
    if money["agent_owed"]:
        lines.append(f"\n💸 Agentlarga to'lanadigan qoldiq: {money_text(money['agent_owed'])} so'm")
    return "\n".join(lines)
