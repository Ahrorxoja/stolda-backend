"""Telegram bilan bog'liq API: Mini App orqali kirish, ulash/uzish, jurnal."""

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telegrambot.webapp import verify_init_data

from . import activity, telegram_link
from .models import ActivityLog, Profile, Restaurant
from .permissions import member_restaurant_ids, require_owner


class TelegramAuthView(APIView):
    """`POST /api/auth/telegram/` — `{init_data, payload?, confirm?}` (restoranlar Mini App'i).

    Javob: `{"step": "ok", access, refresh}` | `{"step": "confirm", ...}` | `{"step": "unlinked"}`.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_scope = "telegram_auth"

    def post(self, request):
        tg_user = verify_init_data(str(request.data.get("init_data", "")), settings.TELEGRAM_BOT_TOKEN)
        if tg_user is None:
            raise PermissionDenied("Telegram imzosi yaroqsiz. Ilovani @Stoldabot'dan qayta oching.")
        payload = str(request.data.get("payload", "") or "")[:80]
        return Response(telegram_link.authenticate(tg_user, payload, bool(request.data.get("confirm"))))


def _qr_data_url(url: str) -> str:
    """Kompyuterda: telefon kamerasi bilan skanerlansa, Telegram telefonda ochiladi."""
    import base64
    import io

    from . import qr as qr_codes

    buffer = io.BytesIO()
    qr_codes.render(url, center="icon", box_size=8).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class TelegramLinkView(APIView):
    """`GET` — holat, `POST` — ulash havolasi (10 daqiqa), `DELETE` — uzish."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        profile = Profile.objects.filter(user=request.user).first()
        linked = bool(profile and profile.telegram_id)
        return Response(
            {
                "linked": linked,
                "username": profile.telegram_username if linked else "",
                "linked_at": profile.telegram_linked_at if linked else None,
                "bot": settings.TELEGRAM_BOT_USERNAME,
            }
        )

    def post(self, request):
        token = telegram_link.new_link_token(request.user)
        return Response(
            {"url": token.url, "qr": _qr_data_url(token.url), "expires_at": token.expires_at},
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request):
        telegram_link.unlink(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _restaurant(request) -> Restaurant:
    restaurant = Restaurant.objects.filter(pk__in=member_restaurant_ids(request.user)).first()
    if restaurant is None:
        raise NotFound("Restoran topilmadi.")
    return restaurant


class ActivityView(APIView):
    """`GET /api/activity/?limit=50` — "Kim nima qildi" (faqat egasi)."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        restaurant = _restaurant(request)
        require_owner(restaurant, request.user, "Jurnalni faqat restoran egasi ko'radi.")
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError:
            limit = 50
        rows = ActivityLog.objects.filter(restaurant=restaurant)[:limit]
        return Response(
            [
                {
                    "id": row.pk,
                    "actor": row.actor,
                    "is_you": row.user_id == request.user.pk,
                    "action": row.action,
                    "action_label": row.get_action_display(),
                    "target": row.target,
                    "detail": row.detail,
                    "can_undo": row.action == ActivityLog.Action.PRICE_CHANGED and row.undone_at is None,
                    "undone": row.undone_at is not None,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        )


class ActivityUndoView(APIView):
    """`POST /api/activity/{id}/undo/` — narx o'zgarishini qaytarish (faqat egasi)."""

    permission_classes = (IsAuthenticated,)

    def post(self, request, pk: int):
        entry = ActivityLog.objects.filter(pk=pk, restaurant=_restaurant(request)).first()
        if entry is None:
            raise NotFound("Yozuv topilmadi.")
        try:
            message = activity.undo(entry, request.user)
        except activity.UndoError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": message})
