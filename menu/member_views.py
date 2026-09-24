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
from .models import Restaurant, RestaurantInvite, RestaurantMember
from .permissions import member_restaurant_ids


class MemberSerializer(serializers.ModelSerializer):
    email = serializers.CharField(source="user.username", read_only=True)
    name = serializers.SerializerMethodField()
    is_you = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantMember
        fields = ("id", "email", "name", "role", "is_you", "created_at")

    def get_name(self, obj) -> str:
        return obj.user.get_full_name() or obj.user.username

    def get_is_you(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and obj.user_id == request.user.id)


class InviteSerializer(serializers.ModelSerializer):
    url = serializers.CharField(read_only=True)

    class Meta:
        model = RestaurantInvite
        fields = ("id", "email", "url", "created_at", "expires_at")


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
        ).select_related("user")
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
            }
        )

    def post(self, request):
        restaurant = _restaurant(request)
        _require_owner(request, restaurant)
        invite = create_invite(
            restaurant, request.data.get("email", ""), invited_by=request.user
        )
        return Response(
            InviteSerializer(invite).data, status=status.HTTP_201_CREATED
        )


class MemberDetailView(APIView):
    """`DELETE /api/members/{id}/` — a'zoni chiqarish (faqat egasi)."""

    permission_classes = (IsAuthenticated,)

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
