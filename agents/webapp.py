"""Agentlar Mini App'i (Telegram Web App) — API.

Agent login qilmaydi: Telegram Mini App'ga `initData` beradi — agentning
Telegram ID si va bot tokeni bilan imzolangan satr. Frontend uni har so'rovda
`X-Telegram-Init-Data` sarlavhasida yuboradi, biz imzoni tekshirib, agentni
`telegram_chat_id` bo'yicha topamiz (shaxsiy chatda chat ID = foydalanuvchi ID).

Bot bilan bir xil qoidalar: ariza kutilayotgan yoki rad etilgan — kira olmaydi,
qoidalarning yangi versiyasiga rozi bo'lmagan — avval botda rozi bo'ladi,
faolligi o'chirilgan — faqat o'z balansi, restoranlari va borgan joylari.
"""

from collections import defaultdict

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from menu.regions import REGIONS, match_region
from telegrambot.webapp import INIT_DATA_MAX_AGE, verify_init_data  # noqa: F401 — testlar shu yerdan oladi
from menu.translations import translate

from . import journal, services
from .models import Agent, AgentEarning, AgentWithdrawal, Place, Visit
from .rules import accepted_current

PLACES_LIMIT = 60


class AgentPrincipal:
    """DRF uchun `request.user` — ichida agent."""

    is_authenticated = True

    def __init__(self, agent: Agent):
        self.agent = agent
        self.pk = self.id = agent.pk  # ScopedRateThrottle kaliti uchun


class TelegramAgentAuthentication(BaseAuthentication):
    def authenticate(self, request):
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        if not init_data:
            return None
        user = verify_init_data(init_data, settings.AGENT_BOT_TOKEN)
        if user is None:
            raise AuthenticationFailed("Telegram imzosi yaroqsiz. Ilovani botdan qayta oching.")
        agent = Agent.objects.filter(telegram_chat_id=str(user["id"])).first()
        if agent is None:
            raise AuthenticationFailed("Siz hali agent emassiz. Botda «📝 Agent bo'lish» ni bosing.")
        return AgentPrincipal(agent), None

    def authenticate_header(self, request):
        return "Telegram"


class IsAgent(BasePermission):
    """Tasdiqlangan, qoidalarga rozi agent. `active_only` — faolligi o'chirilganga yopiq."""

    def has_permission(self, request, view):
        agent = getattr(request.user, "agent", None)
        if agent is None:
            return False
        if agent.rejected_at or agent.is_pending:
            raise PermissionDenied("Arizangiz hali tasdiqlanmagan.")
        if not accepted_current(agent):
            raise PermissionDenied("Qoidalar yangilandi — botda «✅ Qoidalarga roziman» ni bosing.")
        if getattr(view, "active_only", False) and not agent.is_active:
            raise PermissionDenied("Agent hisobingiz faol emas — bu bo'lim yopiq.")
        return True


class AgentView(APIView):
    authentication_classes = (TelegramAgentAuthentication,)
    permission_classes = (IsAgent,)
    active_only = False

    @property
    def agent(self) -> Agent:
        return self.request.user.agent


# ── Ko'rinishlar ───────────────────────────────────────────────────────


def _date(value) -> str:
    return timezone.localtime(value).strftime("%d.%m.%Y") if value else ""


def _visit_row(visit: Visit, agent: Agent) -> dict:
    return {
        "outcome": visit.outcome,
        "outcome_label": visit.get_outcome_display(),
        "agent": journal.first_name(visit.agent),
        "mine": visit.agent_id == agent.pk,
        "comment": visit.comment,
        "ago": journal.ago(visit.created_at),
        "date": _date(visit.created_at),
    }


def place_row(place: Place, agent: Agent, visits_limit: int = 3) -> dict:
    visits = list(place.visits.all())
    held = journal.reservation(place)
    return {
        "id": place.pk,
        "region": place.region,
        "name": place.name,
        "address": place.address,
        "is_client": bool(place.restaurant_id),
        "reserved": (
            {
                "agent": journal.first_name(held.agent),
                "mine": bool(held.agent and held.agent.pk == agent.pk),
                "days_left": held.days_left,
            }
            if held
            else None
        ),
        "visits_count": len(visits),
        "visits": [_visit_row(visit, agent) for visit in visits[:visits_limit]],
    }


class MeView(AgentView):
    """`GET /api/agents/app/me/` — ilova ochilganda: agent, balans, ro'yxatlar uchun ma'lumot."""

    def get(self, request):
        agent = self.agent
        balance = services.balance(agent)
        restaurants = agent.restaurants.all()
        return Response(
            {
                "name": agent.name,
                "first_name": journal.first_name(agent),
                "code": agent.code,
                "link": services.agent_link(agent),
                "is_active": agent.is_active,
                "first_percent": agent.first_percent,
                "percent": agent.percent,
                "card": services.mask_card(agent.card_number) if agent.card_number else "",
                "card_holder": agent.card_holder,
                "min_withdrawal": services.MIN_WITHDRAWAL,
                "balance": {
                    "available": balance.available,
                    "pending": balance.pending,
                    "paid": balance.paid,
                    "this_month": balance.this_month,
                },
                "counts": {
                    "restaurants": restaurants.count(),
                    "paying": restaurants.filter(subscription__status="active").count(),
                    "visits": Visit.objects.filter(agent=agent).count(),
                },
                "regions": REGIONS,
                "outcomes": [{"value": value, "label": label} for value, label in Visit.Outcome.choices],
                "reserve_days": journal.RESERVE_DAYS,
            }
        )


class PlacesView(AgentView):
    """`GET /api/agents/app/places/?region=&q=&mine=1` — borilgan joylar jurnali.

    `mine=1` — faqat o'zi borgan joylar (faolligi o'chirilganga ham ochiq).
    """

    def get(self, request):
        agent = self.agent
        mine = request.query_params.get("mine") == "1"
        if not mine and not agent.is_active:
            raise PermissionDenied("Agent hisobingiz faol emas — faqat o'z joylaringizni ko'rasiz.")
        region = match_region(request.query_params.get("region", "")) or ""
        query = request.query_params.get("q", "").strip()
        key = journal.place_key(query)

        places = Place.objects.prefetch_related("visits__agent")
        if mine:
            places = places.filter(visits__agent=agent).distinct()
        if region:
            places = places.filter(region=region)
        if key:
            places = places.filter(name_key__contains=key)
        places = sorted(places, key=journal._last_visit_time, reverse=True)

        clients = []
        if key and region and not mine:
            shown = {place.restaurant_id for place in places if place.restaurant_id}
            clients = [
                {"name": restaurant.name, "region": restaurant.region}
                for restaurant in journal.clients_like(region, query)
                if restaurant.pk not in shown
            ]
        return Response(
            {
                "total": len(places),
                "places": [place_row(place, agent) for place in places[:PLACES_LIMIT]],
                "clients": clients,
            }
        )


class VisitCreateView(AgentView):
    """`POST /api/agents/app/visits/` — `{region, name, address, outcome, comment, place_id?}`."""

    active_only = True
    throttle_scope = "agent_app_write"

    def post(self, request):
        agent = self.agent
        data = request.data
        errors = {}

        place = None
        if data.get("place_id"):
            place = Place.objects.filter(pk=data.get("place_id")).first()
            if place is None:
                errors["place_id"] = "Bunday joy topilmadi."
            region, name, address = (place.region, place.name, place.address) if place else ("", "", "")
        else:
            region = match_region(str(data.get("region", "")))
            name = journal.clean_latin(str(data.get("name", "")))
            address = journal.clean_latin(str(data.get("address", "")), min_len=3, max_len=120)
            if not region:
                errors["region"] = "Viloyatni tanlang."
            if not name:
                errors["name"] = "Nomni lotin harflarida yozing (2–60 belgi), masalan: Rayhon."
            if not address:
                errors["address"] = "Manzilni lotin harflarida yozing, masalan: Chilonzor 9-kvartal."

        outcome = str(data.get("outcome", ""))
        if outcome not in Visit.Outcome.values:
            errors["outcome"] = "Natijani tanlang."
        comment = " ".join(str(data.get("comment", "")).split())[:300]
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        visit = journal.record_visit(
            agent, region=region, name=name, address=address, outcome=outcome, comment=comment
        )
        place = Place.objects.prefetch_related("visits__agent").get(pk=visit.place_id)
        return Response(place_row(place, agent), status=status.HTTP_201_CREATED)


class PartnersView(AgentView):
    """`GET /api/agents/app/partners/?region=` — stolda.uz'dan foydalanayotgan restoranlar."""

    active_only = True

    def get(self, request):
        region = match_region(request.query_params.get("region", "")) or ""
        queryset = journal.partners().order_by("region", "name")
        if region:
            queryset = queryset.filter(region=region)
        return Response(
            [
                {
                    "name": restaurant.name,
                    "region": restaurant.region,
                    "address": translate(restaurant.address, restaurant.primary_language) or "",
                }
                for restaurant in queryset.only("name", "region", "address", "primary_language")
            ]
        )


class RestaurantsView(AgentView):
    """`GET /api/agents/app/restaurants/` — agent ulagan restoranlar, holati va daromadi."""

    def get(self, request):
        from .bot import STATUS_LABELS, _next_date

        agent = self.agent
        earned = defaultdict(int)
        for row in AgentEarning.objects.filter(agent=agent).values("restaurant_id").annotate(total=Sum("amount")):
            earned[row["restaurant_id"]] = row["total"]
        rows = []
        for restaurant in agent.restaurants.select_related("subscription", "owner__profile").order_by(
            "-agent_attached_at"
        ):
            subscription = getattr(restaurant, "subscription", None)
            status_code = subscription.status if subscription else ""
            profile = getattr(restaurant.owner, "profile", None)
            phones = []
            for phone in (restaurant.phone, getattr(profile, "contact_phone", "")):
                if phone and services.format_phone(phone) not in phones:
                    phones.append(services.format_phone(phone))
            rows.append(
                {
                    "name": restaurant.name,
                    "slug": restaurant.slug,
                    "menu_url": f"{settings.SITE_URL}/{restaurant.slug}",
                    "status": status_code,
                    "status_label": STATUS_LABELS.get(status_code, "—"),
                    "next_date": _next_date(subscription),
                    "earned": earned.get(restaurant.pk, 0),
                    "phones": phones,
                }
            )
        return Response(rows)


class EarningsView(AgentView):
    """`GET /api/agents/app/earnings/` — oxirgi 6 oy, oxirgi yozuvlar va pul yechishlar."""

    def get(self, request):
        agent = self.agent
        earnings = AgentEarning.objects.filter(agent=agent)
        now = timezone.localtime()
        months = []
        for offset in range(5, -1, -1):
            index = now.year * 12 + now.month - 1 - offset
            year, month = index // 12, index % 12 + 1
            start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_index = index + 1
            end = start.replace(year=end_index // 12, month=end_index % 12 + 1)
            total = earnings.filter(created_at__gte=start, created_at__lt=end).aggregate(t=Sum("amount"))["t"] or 0
            months.append({"month": f"{year:04d}-{month:02d}", "amount": total})
        return Response(
            {
                "months": months,
                "recent": [
                    {
                        "restaurant": row.restaurant_name,
                        "kind": row.kind,
                        "amount": row.amount,
                        "date": _date(row.created_at),
                    }
                    for row in earnings.order_by("-created_at")[:20]
                ],
                "withdrawals": [
                    {
                        "amount": row.amount,
                        "status": row.status,
                        "status_label": row.get_status_display(),
                        "card": services.mask_card(row.card_number),
                        "date": _date(row.created_at),
                        "note": row.note,
                    }
                    for row in AgentWithdrawal.objects.filter(agent=agent).order_by("-created_at")[:10]
                ],
            }
        )


class WithdrawView(AgentView):
    """`POST /api/agents/app/withdraw/` — balansdagi hamma pulni so'rash (botdagi kabi)."""

    throttle_scope = "agent_app_write"

    def post(self, request):
        try:
            withdrawal = services.request_withdrawal(self.agent)
        except services.WithdrawalError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"amount": withdrawal.amount, "card": services.mask_card(withdrawal.card_number)},
            status=status.HTTP_201_CREATED,
        )


class CardView(AgentView):
    """`POST /api/agents/app/card/` — `{number, holder}`."""

    throttle_scope = "agent_app_write"

    def post(self, request):
        digits = "".join(ch for ch in str(request.data.get("number", "")) if ch.isdigit())
        holder = " ".join(str(request.data.get("holder", "")).split()).upper()[:64]
        errors = {}
        if len(digits) != 16:
            errors["number"] = "Karta raqami 16 ta raqamdan iborat bo'lishi kerak."
        if len(holder) < 3:
            errors["holder"] = "Ism-familiyani kartadagidek to'liq yozing."
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        agent = self.agent
        agent.card_number = " ".join(digits[i : i + 4] for i in range(0, 16, 4))
        agent.card_holder = holder
        agent.save(update_fields=["card_number", "card_holder"])
        return Response({"card": services.mask_card(agent.card_number), "card_holder": holder})

