"""Telegram hisobini stolda.uz hisobiga ulash va Mini App orqali kirish.

Uch yo'l:

* **Egasi o'zini ulaydi** — admin panelda "Telegram'ni ulash": bir martalik,
  10 daqiqalik `TelegramLinkToken`, havola `t.me/Stoldabot?start=L<token>`.
* **Xodim taklif orqali** — egasi `RestaurantInvite` (pochtasiz) yaratadi,
  havola `t.me/Stoldabot?start=I<token>`. Xodimga Google kerak emas: hisob
  Telegram ID si bilan yaratiladi.
* **Keyingi safar** — Mini App ochilishi bilan: Telegram imzosi → ulangan hisob → JWT.

Ikkala havolada ham ilova avval so'raydi ("Zamin restoraniga ulansinmi?") —
havolani birov ko'rib qolsa ham, tasdiqsiz hech narsa bog'lanmaydi.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    STAFF_PERMISSIONS,
    Profile,
    Restaurant,
    RestaurantInvite,
    RestaurantMember,
    TelegramLinkToken,
)


class LinkError(ValidationError):
    """Foydalanuvchiga ko'rsatiladigan sabab bilan."""

    def __init__(self, message: str):
        super().__init__({"detail": message})


def tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"step": "ok", "access": str(refresh.access_token), "refresh": str(refresh)}


def linked_profile(telegram_id: str) -> Profile | None:
    return Profile.objects.select_related("user").filter(telegram_id=telegram_id).first()


def restaurant_of(user) -> Restaurant | None:
    member = RestaurantMember.objects.select_related("restaurant").filter(user=user).first()
    return member.restaurant if member else None


def new_link_token(user) -> TelegramLinkToken:
    TelegramLinkToken.objects.filter(user=user, used_at__isnull=True).delete()
    return TelegramLinkToken.objects.create(user=user)


def link(user, tg_user: dict) -> Profile:
    """Telegram'ni hisobga bog'laydi. Boshqa hisobga ulangan bo'lsa — xato."""
    telegram_id = str(tg_user["id"])
    other = linked_profile(telegram_id)
    if other and other.user_id != user.pk:
        raise LinkError("Bu Telegram boshqa hisobga ulangan. Avval o'sha hisobda Telegram'ni uzing.")
    profile, _ = Profile.objects.get_or_create(user=user)
    profile.telegram_id = telegram_id
    profile.telegram_username = (tg_user.get("username") or "")[:64]
    profile.telegram_linked_at = timezone.now()
    profile.save(update_fields=["telegram_id", "telegram_username", "telegram_linked_at", "updated_at"])
    return profile


def unlink(user) -> None:
    Profile.objects.filter(user=user).update(telegram_id=None, telegram_username="", telegram_linked_at=None)


def create_telegram_invite(restaurant: Restaurant, name: str, permissions, invited_by) -> RestaurantInvite:
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise LinkError("Xodimning ismini yozing.")
    chosen = [code for code in (permissions or []) if code in STAFF_PERMISSIONS]
    if not chosen:
        raise LinkError("Kamida bitta ruxsatni belgilang.")
    return RestaurantInvite.objects.create(
        restaurant=restaurant, email="", name=name, permissions=chosen, invited_by=invited_by
    )


def authenticate(tg_user: dict, payload: str = "", confirm: bool = False) -> dict:
    """Mini App ochilganda. Natija: `ok` (JWT), `confirm` (tasdiq so'raladi) yoki `unlinked`."""
    telegram_id = str(tg_user["id"])
    linked = linked_profile(telegram_id)

    if payload.startswith("L"):
        token = TelegramLinkToken.objects.select_related("user").filter(token=payload[1:]).first()
        if token is None or not token.is_valid:
            raise LinkError("Havola eskirgan yoki ishlatilgan. Admin panelda «Telegram'ni ulash» ni qayta bosing.")
        restaurant = restaurant_of(token.user)
        if not confirm:
            if linked and linked.user_id == token.user_id:
                return tokens(token.user)
            return {
                "step": "confirm",
                "kind": "link",
                "restaurant": restaurant.name if restaurant else "",
                "account": token.user.get_username(),
            }
        with transaction.atomic():
            link(token.user, tg_user)
            token.used_at = timezone.now()
            token.save(update_fields=["used_at"])
        return tokens(token.user)

    if payload.startswith("I"):
        invite = (
            RestaurantInvite.objects.select_related("restaurant")
            .filter(token=payload[1:], email="")
            .first()
        )
        if invite is None or not invite.is_pending:
            raise LinkError("Taklif eskirgan yoki ishlatilgan. Restoran egasidan yangisini so'rang.")
        if linked:
            memberships = RestaurantMember.objects.filter(user=linked.user)
            if memberships.filter(restaurant=invite.restaurant).exists():
                return tokens(linked.user)
            if memberships.exists():
                raise LinkError("Bu Telegram boshqa restoranga ulangan. Bitta hisob — bitta restoran.")
        if not confirm:
            return {
                "step": "confirm",
                "kind": "invite",
                "restaurant": invite.restaurant.name,
                "name": invite.name,
                # Kodlar — ilova ularni tanlangan tilda ko'rsatadi.
                "permissions": [code for code in invite.permissions if code in STAFF_PERMISSIONS],
            }
        with transaction.atomic():
            invite = RestaurantInvite.objects.select_for_update().get(pk=invite.pk)
            if not invite.is_pending:
                raise LinkError("Taklif allaqachon ishlatilgan.")
            if linked:
                user = linked.user
            else:
                user = get_user_model().objects.create_user(
                    username=f"tg{telegram_id}",
                    first_name=(tg_user.get("first_name") or "")[:150],
                    last_name=(tg_user.get("last_name") or "")[:150],
                )
                user.set_unusable_password()
                user.save(update_fields=["password"])
                Profile.objects.get_or_create(user=user, defaults={"full_name": invite.name})
                link(user, tg_user)
            RestaurantMember.objects.create(
                restaurant=invite.restaurant,
                user=user,
                role=RestaurantMember.Role.MANAGER,
                permissions=list(invite.permissions or []),
            )
            invite.accepted_at = timezone.now()
            invite.save(update_fields=["accepted_at"])
        return tokens(user)

    if linked:
        return tokens(linked.user)
    return {"step": "unlinked"}
