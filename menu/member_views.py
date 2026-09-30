"""Xodimlar bo'limi: a'zolar ro'yxati, taklif qilish, chiqarish.

Ko'rish — har qanday a'zoga, o'zgartirish — faqat egasiga.
"""

from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .members import (
    InviteError,
    accept_invite,
    create_invite,
    remove_member,
)
from .models import STAFF_PERMISSIONS, Restaurant, RestaurantInvite, RestaurantMember
from .permissions import member_restaurant_ids, permissions_of
from .telegram_link import create_telegram_invite

#: Xodim qo'shishdagi tayyor shablonlar.
TEMPLATES = [
    {"id": "manager", "label": "Menejer", "permissions": list(STAFF_PERMISSIONS)},
    {"id": "kitchen", "label": "Oshxona", "permissions": ["stoplist"]},
]


class MemberSerializer(serializers.ModelSerializer):
    email = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    is_you = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    telegram = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantMember
        fields = ("id", "email", "name", "role", "is_you", "permissions", "notify_changes", "telegram", "created_at")

    def get_email(self, obj) -> str:
        # Telegram orqali qo'shilgan xodimda pochta yo'q — `tg123` ko'rsatilmaydi.
        return "" if obj.user.username.startswith("tg") and "@" not in obj.user.username else obj.user.username

    def get_name(self, obj) -> str:
        profile = getattr(obj.user, "profile", None)
        return (profile.full_name if profile else "") or obj.user.get_full_name() or obj.user.username

    def get_permissions(self, obj) -> list[str]:
        return permissions_of(obj)

    def get_telegram(self, obj) -> str | None:
        """Ulangan bo'lsa — `@username` (yoki bo'sh satr), ulanmagan — `None`."""
        profile = getattr(obj.user, "profile", None)
        if not profile or not profile.telegram_id:
            return None
        return f"@{profile.telegram_username}" if profile.telegram_username else ""

    def get_is_you(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and obj.user_id == request.user.id)


class InviteSerializer(serializers.ModelSerializer):
    url = serializers.CharField(read_only=True)
    is_telegram = serializers.BooleanField(read_only=True)

    class Meta:
        model = RestaurantInvite
        fields = ("id", "email", "name", "permissions", "is_telegram", "url", "created_at", "expires_at")


def _restaurant(request) -> Restaurant:
    restaurant = Restaurant.objects.filter(
        pk__in=member_restaurant_ids(request.user)
    ).first()
    if restaurant is None:
        raise NotFound("Restoran topilmadi.")
    return restaurant


def _require_owner(request, restaurant: Restaurant) -> None:
    member = RestaurantMember.objects.filter(
        restaurant=restaurant, user=request.user
    ).first()
    if member is None or not member.is_owner:
        raise PermissionDenied("Bu amal faqat restoran egasi uchun.")


class MemberListView(APIView):
    """`GET /api/members/` — a'zolar va kutilayotgan takliflar.

    `POST` — yangi taklif (faqat egasi).
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        restaurant = _restaurant(request)
        members = RestaurantMember.objects.filter(
            restaurant=restaurant
        ).select_related("user", "user__profile")
        invites = RestaurantInvite.objects.filter(
            restaurant=restaurant, accepted_at__isnull=True
        )
        you = members.filter(user=request.user).first()
        return Response(
            {
                "role": you.role if you else None,
                "members": MemberSerializer(
                    members, many=True, context={"request": request}
                ).data,
                "invites": InviteSerializer(invites, many=True).data,
                "permission_choices": [{"code": code, "label": label} for code, label in STAFF_PERMISSIONS.items()],
                "templates": TEMPLATES,
            }
        )

    def post(self, request):
        restaurant = _restaurant(request)
        _require_owner(request, restaurant)
        if request.data.get("channel") == "telegram":
            invite = create_telegram_invite(
                restaurant,
                request.data.get("name", ""),
                request.data.get("permissions") or [],
                invited_by=request.user,
            )
        else:
            invite = create_invite(
                restaurant, request.data.get("email", ""), invited_by=request.user
            )
        return Response(
            InviteSerializer(invite).data, status=status.HTTP_201_CREATED
        )


class MemberDetailView(APIView):
    """`PATCH /api/members/{id}/` — ruxsatlar / xabarlar, `DELETE` — chiqarish (faqat egasi)."""

    permission_classes = (IsAuthenticated,)

    def patch(self, request, pk: int):
        restaurant = _restaurant(request)
        _require_owner(request, restaurant)
        member = RestaurantMember.objects.filter(pk=pk, restaurant=restaurant).select_related("user").first()
        if member is None:
            raise NotFound("A'zo topilmadi.")
        fields = []
        if "permissions" in request.data:
            if member.is_owner:
                raise InviteError({"detail": "Egasining ruxsatlari o'zgarmaydi — unda hammasi bor."})
            chosen = [code for code in request.data.get("permissions") or [] if code in STAFF_PERMISSIONS]
            member.permissions = chosen
            fields.append("permissions")
        if "notify_changes" in request.data:
            member.notify_changes = bool(request.data.get("notify_changes"))
            fields.append("notify_changes")
        if fields:
            member.save(update_fields=fields)
        return Response(MemberSerializer(member, context={"request": request}).data)

    def delete(self, request, pk: int):
        restaurant = _restaurant(request)
        _require_owner(request, restaurant)
        member = RestaurantMember.objects.filter(
            pk=pk, restaurant=restaurant
        ).first()
        if member is None:
            raise NotFound("A'zo topilmadi.")
        if member.user_id == request.user.id:
            raise InviteError({"detail": "O'zingizni ro'yxatdan chiqara olmaysiz."})
        remove_member(member)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InviteDetailView(APIView):
    """`DELETE /api/invites/{id}/` — taklifni bekor qilish (faqat egasi)."""

    permission_classes = (IsAuthenticated,)

    def delete(self, request, pk: int):
        restaurant = _restaurant(request)
        _require_owner(request, restaurant)
        deleted, _ = RestaurantInvite.objects.filter(
            pk=pk, restaurant=restaurant, accepted_at__isnull=True
        ).delete()
        if not deleted:
            raise NotFound("Taklif topilmadi.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitePreviewView(APIView):
    """`GET /api/invites/{token}/` — havola ochilganda ko'rsatiladigan ma'lumot.

    Kirmagan odam ham ko'radi: qaysi restoranga, qaysi pochtaga taklif.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_scope = "signup"

    def get(self, request, token: str):
        invite = RestaurantInvite.objects.filter(token=token).select_related(
            "restaurant"
        ).first()
        if invite is None:
            raise NotFound("Taklif topilmadi.")
        return Response(
            {
                "restaurant": invite.restaurant.name,
                "email": invite.email,
                "valid": invite.is_pending,
            }
        )


class InviteAcceptView(APIView):
    """`POST /api/invites/{token}/accept/` — taklifni qabul qilish."""

    permission_classes = (IsAuthenticated,)

    def post(self, request, token: str):
        invite = RestaurantInvite.objects.filter(token=token).select_related(
            "restaurant"
        ).first()
        if invite is None:
            raise NotFound("Taklif topilmadi.")
        member = accept_invite(invite, request.user)
        return Response(
            {"restaurant": member.restaurant.slug, "role": member.role},
            status=status.HTTP_200_OK,
        )
