"""Borilgan joylar jurnali: yozish, qidirish, band, mijozlar bilan bog'lash.

Hamma agentlar ko'radi — bir restoranga bir necha agent borib qolmasin va
bir-birining tajribasidan foydalansin ("QR allaqachon bor", "rad etdi").
Oxirgi natija "Qiziqdi" bo'lsa, joy o'sha agentga `RESERVE_DAYS` kun band.
"""

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from menu.models import Restaurant, Subscription
from telegrambot import TelegramError, get_bot

from .models import Agent, Place, Visit, place_key

logger = logging.getLogger(__name__)

RESERVE_DAYS = 14

_APOSTROPHES = re.compile(r"[ʻʼ’‘`]")
_LATIN = re.compile(r"^[A-Za-z0-9 '\-&.,()/\"№#]+$")


def clean_latin(text: str, *, min_len: int = 2, max_len: int = 60) -> str:
    """Faqat lotin harflari (o'zbek apostrofi bilan). Mos kelmasa — bo'sh."""
    value = " ".join(_APOSTROPHES.sub("'", text or "").split())
    if not (min_len <= len(value) <= max_len) or not _LATIN.match(value):
        return ""
    if len(re.findall(r"[A-Za-z]", value)) < 2:
        return ""
    return value


@dataclass
class Reservation:
    agent: Agent | None
    days_left: int


def reservation(place: Place) -> Reservation | None:
    """Oxirgi tashrif "Qiziqdi" va 14 kun o'tmagan bo'lsa — kimga band."""
    last = place.visits.select_related("agent").first()
    if last is None or last.outcome != Visit.Outcome.INTERESTED:
        return None
    ends = last.created_at + timedelta(days=RESERVE_DAYS)
    now = timezone.now()
    if ends <= now:
        return None
    return Reservation(agent=last.agent, days_left=max((ends - now).days, 1))


def same_name_places(region: str, name: str) -> list[Place]:
    return list(Place.objects.filter(region=region, name_key=place_key(name)).order_by("address"))


PARTNER_STATUSES = [Subscription.Status.TRIALING, Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE]


def partners():
    """stolda.uz'dan foydalanayotgan restoranlar (namuna/yashirinlari emas)."""
    return Restaurant.objects.filter(subscription__status__in=PARTNER_STATUSES, is_active=True, is_listed=True)


def clients_like(region: str, query: str, limit: int = 5) -> list[Restaurant]:
    """stolda.uz'dan foydalanayotgan restoranlar — shu viloyatda (yoki viloyati noma'lum)."""
    key = place_key(query)
    queryset = partners().filter(models_region_q(region))
    found = []
    for restaurant in queryset.only("name", "region"):
        if key and key in place_key(restaurant.name):
            found.append(restaurant)
            if len(found) >= limit:
                break
    return found


def models_region_q(region: str):
    from django.db.models import Q

    return Q(region=region) | Q(region="")


def search(region: str, query: str = "", limit: int = 8) -> tuple[list[Restaurant], list[Place]]:
    """Qidiruv: (mijozlar, joylar). So'rovsiz — viloyatdagi oxirgi tashriflar."""
    places = Place.objects.filter(region=region).prefetch_related("visits__agent")
    key = place_key(query)
    if key:
        places = [place for place in places if key in place.name_key]
        clients = clients_like(region, query)
    else:
        places = list(places)
        clients = []
    places.sort(key=lambda place: _last_visit_time(place), reverse=True)
    return clients, places[:limit]


def _last_visit_time(place: Place):
    visits = list(place.visits.all())
    return visits[0].created_at if visits else place.created_at


@transaction.atomic
def record_visit(agent: Agent, *, region: str, name: str, address: str, outcome: str, comment: str) -> Visit:
    place, _ = Place.objects.get_or_create(
        region=region,
        name_key=place_key(name),
        address_key=place_key(address),
        defaults={"name": name, "address": address, "created_by": agent},
    )
    return Visit.objects.create(place=place, agent=agent, outcome=outcome, comment=comment)


def link_restaurant(restaurant: Restaurant, agent: Agent) -> None:
    """Agent kodi bilan ro'yxatdan o'tgan restoran — jurnaldagi joyni "Ulandi" qiladi.

    Joy boshqa agentga band bo'lsa, platforma egasiga ogohlantirish boradi —
    pul kodi bilan ulagan agentga yoziladi, qoida buzilgan bo'lsa egasi hal qiladi.
    """
    if not restaurant.region:
        return
    candidates = [
        place
        for place in Place.objects.filter(region=restaurant.region, name_key=place_key(restaurant.name))
        if place.restaurant_id in (None, restaurant.pk)
    ]
    for place in candidates:
        held = reservation(place)
        place.restaurant = restaurant
        place.save(update_fields=["restaurant"])
        Visit.objects.create(
            place=place,
            agent=agent,
            outcome=Visit.Outcome.CONNECTED,
            comment="Ro'yxatdan o'tdi (avtomatik)",
        )
        if held and held.agent and held.agent.pk != agent.pk:
            _warn_owner(
                f"⚠️ <b>Band qilingan joy boshqa agent orqali ulandi</b>\n\n"
                f"Joy: {place.name} — {place.address} ({place.region})\n"
                f"Band qilgan: {held.agent.name} ({held.agent.code})\n"
                f"Ulagan: {agent.name} ({agent.code})\n\n"
                f"Daromad {agent.code} ga yoziladi. Kerak bo'lsa Django admin'da restoranning agentini o'zgartiring."
            )


def _warn_owner(text: str) -> None:
    def send():
        try:
            get_bot().send_message(text)
        except TelegramError as error:
            logger.warning("Ogohlantirish yuborilmadi: %s", error)

    transaction.on_commit(send)


# ── Matnlar ────────────────────────────────────────────────────────────


def ago(when) -> str:
    days = (timezone.now() - when).days
    if days <= 0:
        return "bugun"
    if days == 1:
        return "kecha"
    if days < 30:
        return f"{days} kun oldin"
    return f"{days // 30} oy oldin"


def first_name(agent: Agent | None) -> str:
    return (agent.name.split() or ["—"])[0] if agent and agent.name else "—"


def place_card(place: Place, visits_limit: int = 3) -> str:
    lines = [f"📍 <b>{place.name}</b> — {place.address}"]
    if place.restaurant_id:
        lines.append("   🟢 stolda.uz mijozi")
    held = reservation(place)
    if held:
        lines.append(f"   🟡 {first_name(held.agent)} bilan ishlanmoqda — {held.days_left} kun qoldi")
    for visit in list(place.visits.all())[:visits_limit]:
        lines.append(f"   {visit.get_outcome_display()} · {first_name(visit.agent)} · {ago(visit.created_at)}")
        if visit.comment:
            lines.append(f"   «{visit.comment}»")
    return "\n".join(lines)


def search_text(region: str, query: str) -> str:
    clients, places = search(region, query)
    title = f"🔍 <b>{region}</b>" + (f" — «{query}»" if query else " — oxirgi tashriflar")
    parts = [title]
    shown = {place.restaurant_id for place in places if place.restaurant_id}
    for restaurant in (client for client in clients if client.pk not in shown):
        where = restaurant.region or "viloyati ko'rsatilmagan"
        parts.append(f"🟢 <b>{restaurant.name}</b> — stolda.uz mijozi ({where})\n   Bormang — bu restoran allaqachon biz bilan.")
    for place in places:
        parts.append(place_card(place))
    if len(parts) == 1:
        parts.append("Hech narsa topilmadi — bu joyga hali hech kim bormagan. Bemalol boring ✅")
    return "\n\n".join(parts)


def my_visits_parts(agent: Agent, limit: int = 20) -> list[str]:
    """Agentning o'zi borgan joylar — har joy bir marta, oxirgi tashrifi bilan."""
    visits = Visit.objects.filter(agent=agent).select_related("place")
    total = visits.count()
    if not total:
        return ["📒 Hali borgan joyingiz yozilmagan. Borgan har bir joyni «➕ Borgan joyim» orqali yozing."]
    latest: dict[int, Visit] = {}
    for visit in visits:
        latest.setdefault(visit.place_id, visit)
    parts = [f"📒 <b>Borgan joylaringiz</b> — {len(latest)} ta joy, {total} ta tashrif"]
    for visit in list(latest.values())[:limit]:
        place = visit.place
        lines = [f"📍 <b>{place.name}</b> — {place.address} ({place.region})"]
        if place.restaurant_id:
            lines.append("   🟢 stolda.uz mijozi")
        held = reservation(place)
        if held and held.agent and held.agent.pk == agent.pk:
            lines.append(f"   🟡 Sizga band — {held.days_left} kun qoldi")
        lines.append(f"   {visit.get_outcome_display()} · {ago(visit.created_at)}")
        if visit.comment:
            lines.append(f"   «{visit.comment}»")
        parts.append("\n".join(lines))
    if len(latest) > limit:
        parts.append(f"… va yana {len(latest) - limit} ta joy (oxirgi {limit} tasi ko'rsatildi).")
    return parts


def places_parts(region: str, limit: int = 30) -> list[str]:
    """Viloyatdagi hamma agentlar borgan joylar — oxirgi tashrif bo'yicha."""
    places = list(Place.objects.filter(region=region).prefetch_related("visits__agent"))
    if not places:
        return [f"🗺 <b>{region}</b>\n\nBu viloyatda hali hech kim bormagan — birinchi bo'ling ✅"]
    places.sort(key=_last_visit_time, reverse=True)
    counts: dict[str, int] = {}
    for place in places:
        visits = list(place.visits.all())
        if visits:
            label = Visit.Outcome(visits[0].outcome).label
            counts[label] = counts.get(label, 0) + 1
    summary = " · ".join(f"{label.split()[0]} {n}" for label, n in counts.items())
    parts = [f"🗺 <b>{region}</b> — {len(places)} ta joy\n{summary}\n\n🟢 mijoz · 🟡 band — bormang"]
    parts += [place_card(place, visits_limit=1) for place in places[:limit]]
    if len(places) > limit:
        parts.append(f"… va yana {len(places) - limit} ta. Aniq joyni «🔍 Qidirish» bilan toping.")
    return parts


def partners_parts(region: str = "") -> list[str]:
    """stolda.uz hamkorlari — viloyat bo'yicha (bo'sh — hamma viloyatlar)."""
    from menu.translations import translate

    queryset = partners().order_by("region", "name")
    if region:
        queryset = queryset.filter(region=region)
    restaurants = list(queryset.only("name", "region", "address", "primary_language"))
    title = f"🤝 <b>Hamkorlarimiz</b> — {region or 'hamma viloyatlar'} ({len(restaurants)})"
    if not restaurants:
        return [title + "\n\nBu viloyatda hali hamkorimiz yo'q — birinchisini siz ulang 💪"]
    parts = [title + "\n\nBular stolda.uz'dan foydalanadi — ularga bormang."]
    groups: dict[str, list[str]] = {}
    for restaurant in restaurants:
        address = translate(restaurant.address, restaurant.primary_language) or ""
        line = f"🟢 <b>{restaurant.name}</b>" + (f" — {address}" if address else "")
        groups.setdefault(restaurant.region or "Viloyati ko'rsatilmagan", []).append(line)
    for name, lines in groups.items():
        parts.append((f"<b>{name}</b>\n" if not region else "") + "\n".join(lines))
    return parts


def chunks(parts: list[str], size: int = 3800) -> list[str]:
    """Telegram 4096 belgidan uzun xabar olmaydi — qismlarni bo'lib beradi."""
    messages, current = [], ""
    for part in parts:
        part = part[:size]
        if current and len(current) + len(part) + 2 > size:
            messages.append(current)
            current = part
        else:
            current = f"{current}\n\n{part}" if current else part
    if current:
        messages.append(current)
    return messages
