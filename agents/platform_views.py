"""Platforma paneli (faqat stolda.uz egasi) — agentlar bilan ishlash.

* `GET  /api/platform/agents/`                   — tanlash ro'yxati (restoranni o'tkazish uchun)
* `GET  /api/platform/agents/{id}/`              — agent sahifasi: restoranlar, tashriflar, daromad
* `POST /api/platform/agents/{id}/active/`       — faolsizlantirish / qayta yoqish
* `GET  /api/platform/restaurants/?q=`           — restoran qidiruvi: agenti kim
* `POST /api/platform/restaurants/{id}/agent/`   — restoranni boshqa agentga o'tkazish (yoki olib tashlash)

Foizlar, yozuvlarni qo'lda tuzatish kabi kamdan-kam ishlar — Django admin'da.
"""

from collections import defaultdict

from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from menu.admin_views import IsPlatformOwner
from menu.models import Profile, Restaurant
from menu.platform import shift_month, month_range

from . import services
from .journal import first_name
from .models import Agent, AgentEarning, AgentWithdrawal, Visit

MONTHS = 12


def _date(value) -> str:
    return timezone.localtime(value).strftime("%d.%m.%Y") if value else ""


def _agent_status(agent: Agent) -> str:
    if agent.rejected_at:
        return "rejected"
    if agent.is_pending:
        return "pending"
    return "active" if agent.is_active else "inactive"


def _agent_short(agent: Agent | None) -> dict | None:
    if agent is None:
        return None
    return {"id": agent.pk, "name": agent.name, "code": agent.code or "", "is_active": agent.is_active}


class PlatformView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformOwner)


class AgentListView(PlatformView):
    def get(self, request):
        agents = Agent.objects.exclude(code__isnull=True).order_by("-is_active", "name")
        return Response([_agent_short(agent) for agent in agents])


class AgentDetailView(PlatformView):
    def get(self, request, pk: int):
        from .bot import STATUS_LABELS, _next_date

        agent = get_object_or_404(Agent, pk=pk)
        earnings = AgentEarning.objects.filter(agent=agent)
        by_restaurant = defaultdict(int)
        for row in earnings.values("restaurant_id").annotate(total=Sum("amount")):
            by_restaurant[row["restaurant_id"]] = row["total"] or 0

        restaurants = []
        for restaurant in agent.restaurants.select_related("subscription", "owner__profile").order_by("-agent_attached_at"):
            subscription = getattr(restaurant, "subscription", None)
            code = subscription.status if subscription else ""
            profile = getattr(restaurant.owner, "profile", None)
            restaurants.append(
                {
                    "id": restaurant.pk,
                    "name": restaurant.name,
                    "slug": restaurant.slug,
                    "region": restaurant.region,
                    "status": code,
                    "status_label": STATUS_LABELS.get(code, "—").split(" ", 1)[-1],
                    "next_date": _next_date(subscription),
                    "phone": services.format_phone(restaurant.phone) if restaurant.phone else "",
                    "telegram": bool(profile and profile.telegram_id),
                    "earned": by_restaurant.get(restaurant.pk, 0),
                    "attached": _date(restaurant.agent_attached_at),
                }
            )

        now = timezone.localtime()
        months = []
        for offset in range(MONTHS - 1, -1, -1):
            year, month = shift_month(now.year, now.month, -offset)
            start, end = month_range(year, month)
            total = earnings.filter(created_at__gte=start, created_at__lt=end).aggregate(t=Sum("amount"))["t"] or 0
            months.append({"month": f"{year:04d}-{month:02d}", "amount": total})

        balance = services.balance(agent)
        visits = Visit.objects.filter(agent=agent).select_related("place").order_by("-created_at")
        return Response(
            {
                "id": agent.pk,
                "name": agent.name,
                "code": agent.code or "",
                "phone": services.format_phone(agent.phone) if agent.phone else "",
                "region": agent.city,
                "telegram_username": agent.telegram_username,
                "status": _agent_status(agent),
                "is_active": agent.is_active,
                "note": agent.note,
                "joined": _date(agent.approved_at or agent.created_at),
                "first_percent": agent.first_percent,
                "percent": agent.percent,
                "card": services.mask_card(agent.card_number) if agent.card_number else "",
                "balance": {
                    "available": balance.available,
                    "pending": balance.pending,
                    "paid": balance.paid,
                    "this_month": balance.this_month,
                    "total": earnings.aggregate(t=Sum("amount"))["t"] or 0,
                },
                "counts": {
                    "restaurants": len(restaurants),
                    "paying": sum(row["status"] == "active" for row in restaurants),
                    "visits": visits.count(),
                    "visits_month": visits.filter(created_at__gte=month_range(now.year, now.month)[0]).count(),
                },
                "restaurants": restaurants,
                "months": months,
                "visits": [
                    {
                        "place": visit.place.name,
                        "address": visit.place.address,
                        "region": visit.place.region,
                        "outcome": visit.outcome,
                        "outcome_label": visit.get_outcome_display(),
                        "comment": visit.comment,
                        "date": _date(visit.created_at),
                    }
                    for visit in visits[:40]
                ],
                "withdrawals": [
                    {
                        "amount": row.amount,
                        "status": row.status,
                        "status_label": row.get_status_display(),
                        "date": _date(row.created_at),
                    }
                    for row in AgentWithdrawal.objects.filter(agent=agent).order_by("-created_at")[:10]
                ],
            }
        )


class AgentActiveView(PlatformView):
    """`{"is_active": false}` — yangi daromad yozilmaydi; topgan balansini yechib olishi mumkin."""

    def post(self, request, pk: int):
        agent = get_object_or_404(Agent, pk=pk)
        if agent.is_pending or agent.rejected_at:
            return Response({"detail": "Ariza hali qabul qilinmagan."}, status=status.HTTP_400_BAD_REQUEST)
        value = bool(request.data.get("is_active"))
        if agent.is_active != value:
            agent.is_active = value
            agent.save(update_fields=["is_active"])
            services.notify_agent(
                agent,
                "✅ Agent hisobingiz qayta faollashtirildi. Ishni davom ettirishingiz mumkin."
                if value
                else "⏸ Agent hisobingiz vaqtincha faolsizlantirildi — yangi daromad yozilmaydi. "
                "Topgan balansingizni yechib olishingiz mumkin. Savollar: @aha_daragoy",
            )
        return Response({"is_active": agent.is_active})


class RestaurantSearchView(PlatformView):
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        restaurants = Restaurant.objects.select_related("agent", "subscription", "owner").order_by("-created_at")
        if query:
            restaurants = restaurants.filter(
                Q(name__icontains=query) | Q(slug__icontains=query) | Q(phone__icontains=query) | Q(owner__username__icontains=query)
            )
        linked = set(
            Profile.objects.filter(telegram_id__isnull=False).values_list("user_id", flat=True)
        )
        return Response(
            [
                {
                    "id": restaurant.pk,
                    "name": restaurant.name,
                    "slug": restaurant.slug,
                    "region": restaurant.region,
                    "status": getattr(getattr(restaurant, "subscription", None), "status", ""),
                    "owner": restaurant.owner.get_username() if restaurant.owner else "",
                    "telegram": restaurant.owner_id in linked,
                    "agent": _agent_short(restaurant.agent),
                }
                for restaurant in restaurants[:30]
            ]
        )


class RestaurantAgentView(PlatformView):
    """`{"agent": <id> | null}` — keyingi to'lovlardan ulush yangi agentga yoziladi."""

    def post(self, request, pk: int):
        restaurant = get_object_or_404(Restaurant, pk=pk)
        raw = request.data.get("agent")
        new = get_object_or_404(Agent, pk=raw, code__isnull=False) if raw else None
        old = restaurant.agent
        if (old.pk if old else None) == (new.pk if new else None):
            return Response({"agent": _agent_short(new)})

        restaurant.agent = new
        restaurant.agent_attached_at = timezone.now() if new else None
        restaurant.save(update_fields=["agent", "agent_attached_at"])
        if old:
            services.notify_agent(
                old, f"ℹ️ <b>{restaurant.name}</b> boshqa agentga o'tkazildi. Savollar: @aha_daragoy"
            )
        if new:
            services.notify_agent(
                new,
                f"🎉 <b>{restaurant.name}</b> sizga biriktirildi. Keyingi to'lovlaridan ulushingiz yoziladi. "
                f"Egasi bilan tanishib qo'ying 🙂",
            )
        return Response({"agent": _agent_short(new), "previous": first_name(old) if old else ""})
