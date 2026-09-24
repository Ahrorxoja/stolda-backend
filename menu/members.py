"""Restoran a'zolari va takliflar bilan bog'liq amallar.

Qoidalar bitta joyda tursin: ham API, ham Google bilan kirish oqimi
shu funksiyalarni chaqiradi.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Restaurant, RestaurantInvite, RestaurantMember


class InviteError(ValidationError):
    """Taklif qabul qilinmadi — sabab foydalanuvchiga ko'rsatiladi."""


def has_restaurant(user) -> bool:
    return RestaurantMember.objects.filter(user=user).exists()


def create_invite(restaurant: Restaurant, email: str, invited_by) -> RestaurantInvite:
    """Menejerni taklif qiladi.

    Hozircha bir hisob — bitta restoran, shuning uchun allaqachon restorani
    bor pochtani taklif qilib bo'lmaydi.
    """
    email = (email or "").strip().lower()
    if not email:
        raise InviteError({"email": "Pochtani yozing."})

    existing = get_user_model().objects.filter(username__iexact=email).first()
    if existing and RestaurantMember.objects.filter(
        restaurant=restaurant, user=existing
    ).exists():
        raise InviteError({"email": "Bu odam allaqachon ro'yxatda."})
    if existing and has_restaurant(existing):
        raise InviteError(
            {"email": "Bu hisob boshqa restoranga biriktirilgan."}
        )

    # Eskirgan taklif turgan bo'lsa yangisiga o'rin bo'shatamiz.
    RestaurantInvite.objects.filter(
        restaurant=restaurant, email=email, accepted_at__isnull=True
    ).delete()

    try:
        return RestaurantInvite.objects.create(
            restaurant=restaurant, email=email, invited_by=invited_by
        )
    except IntegrityError as error:  # pragma: no cover — yuqorida tozalandi
        raise InviteError({"email": "Bu pochtaga taklif allaqachon yuborilgan."}) from error


def pending_invites_for(email: str):
    """Shu pochtaga yuborilgan, hali eskirmagan takliflar."""
    return RestaurantInvite.objects.filter(
        email=(email or "").strip().lower(),
        accepted_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).select_related("restaurant")


def accept_invite(invite: RestaurantInvite, user) -> RestaurantMember:
    """Taklifni qabul qiladi va a'zolik beradi."""
    if invite.accepted_at is not None:
        raise InviteError({"detail": "Bu taklif allaqachon ishlatilgan."})
    if invite.expires_at <= timezone.now():
        raise InviteError({"detail": "Taklif muddati tugagan. Egasidan yangisini so'rang."})
    if (user.username or "").lower() != invite.email:
        raise InviteError(
            {"detail": "Taklif boshqa pochtaga yuborilgan. O'sha hisob bilan kiring."}
        )

    member = RestaurantMember.objects.filter(
        restaurant=invite.restaurant, user=user
    ).first()
    if member:
        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_at"])
        return member

    if has_restaurant(user):
        raise InviteError({"detail": "Bu hisob boshqa restoranga biriktirilgan."})

    with transaction.atomic():
        member = RestaurantMember.objects.create(
            restaurant=invite.restaurant,
            user=user,
            role=RestaurantMember.Role.MANAGER,
        )
        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_at"])
    return member


def accept_pending_for(user) -> RestaurantMember | None:
    """Kirish paytida: shu pochtaga taklif bo'lsa, o'zi qabul qilinadi.

    Havolasiz ham ishlashi uchun — menejer oddiygina Google bilan kirsa
    bo'ladi. Allaqachon restorani bo'lsa hech narsa qilinmaydi.
    """
    if has_restaurant(user):
        return None
    invite = pending_invites_for(user.username).first()
    if invite is None:
        return None
    try:
        return accept_invite(invite, user)
    except InviteError:
        return None


def remove_member(member: RestaurantMember) -> None:
    """A'zoni chiqaradi. Oxirgi egasini chiqarib bo'lmaydi."""
    if member.is_owner:
        owners = RestaurantMember.objects.filter(
            restaurant=member.restaurant, role=RestaurantMember.Role.OWNER
        ).count()
        if owners <= 1:
            raise InviteError({"detail": "Restoranning yagona egasini o'chirib bo'lmaydi."})
    member.delete()
