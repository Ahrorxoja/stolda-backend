"""Kim qaysi restoranni boshqara oladi.

Ilgari bitta `Restaurant.owner` tekshirilardi. Endi restoranda bir nechta
a'zo bo'ladi (`RestaurantMember`), shuning uchun tekshiruv a'zolik bo'yicha.
Pul bilan bog'liq amallar (to'lov, tarif) va admin qo'shish esa faqat
`owner` rolidagi a'zoda qoladi.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import STAFF_PERMISSIONS, Category, Dish, Restaurant, RestaurantMember


def restaurant_of(obj) -> Restaurant | None:
    """Obyekt qaysi restoranga tegishli."""
    if isinstance(obj, Restaurant):
        return obj
    if isinstance(obj, Category):
        return obj.restaurant
    if isinstance(obj, Dish):
        return obj.category.restaurant
    return None


def member_restaurant_ids(user):
    """Foydalanuvchi a'zo bo'lgan restoranlar."""
    if not user or not user.is_authenticated:
        return RestaurantMember.objects.none().values_list("restaurant_id", flat=True)
    return RestaurantMember.objects.filter(user=user).values_list(
        "restaurant_id", flat=True
    )


def restaurants_for(user):
    """Foydalanuvchi boshqara oladigan restoranlar."""
    return Restaurant.objects.filter(pk__in=member_restaurant_ids(user))


def role_in(restaurant: Restaurant, user) -> str | None:
    """`"owner"`, `"manager"` yoki a'zo bo'lmasa `None`."""
    if not user or not user.is_authenticated or restaurant is None:
        return None
    member = RestaurantMember.objects.filter(restaurant=restaurant, user=user).first()
    return member.role if member else None


def is_member(restaurant: Restaurant, user) -> bool:
    return role_in(restaurant, user) is not None


def is_owner(restaurant: Restaurant, user) -> bool:
    return role_in(restaurant, user) == RestaurantMember.Role.OWNER


def member_of(restaurant: Restaurant | None, user) -> RestaurantMember | None:
    if not user or not user.is_authenticated or restaurant is None:
        return None
    return RestaurantMember.objects.filter(restaurant=restaurant, user=user).first()


def permissions_of(member: RestaurantMember | None) -> list[str]:
    """Egasida hammasi, xodimda — egasi belgilagani."""
    if member is None:
        return []
    if member.is_owner:
        return list(STAFF_PERMISSIONS)
    return [code for code in (member.permissions or []) if code in STAFF_PERMISSIONS]


def can(restaurant: Restaurant | None, user, permission: str) -> bool:
    return permission in permissions_of(member_of(restaurant, user))


#: Ruxsat yo'q bo'lganda ko'rsatiladigan matn.
DENIED = {
    "stoplist": "Stop-list'ni o'zgartirishga ruxsatingiz yo'q.",
    "dishes": "Taom qo'shish va tahrirlashga ruxsatingiz yo'q.",
    "prices": "Narxni o'zgartirishga ruxsatingiz yo'q.",
    "categories": "Kategoriyalarni o'zgartirishga ruxsatingiz yo'q.",
    "stats": "Statistikani ko'rishga ruxsatingiz yo'q.",
    "qr": "QR kod bo'limiga ruxsatingiz yo'q.",
}


def require(restaurant: Restaurant | None, user, permission: str) -> None:
    """Ruxsat bo'lmasa — 403. Egasi belgilamagan ishni xodim qila olmaydi (serverda ham)."""
    if not can(restaurant, user, permission):
        raise PermissionDenied(DENIED.get(permission, "Bu amalga ruxsatingiz yo'q. Restoran egasiga murojaat qiling."))


def require_owner(restaurant: Restaurant | None, user, message: str = "Bu amal faqat restoran egasi uchun.") -> None:
    if not is_owner(restaurant, user):
        raise PermissionDenied(message)


class IsRestaurantMember(BasePermission):
    """Faqat o'zi a'zo bo'lgan restoranning ma'lumotlari."""

    message = "Bu ma'lumot sizning restoraningizga tegishli emas."

    def has_object_permission(self, request, view, obj) -> bool:
        return is_member(restaurant_of(obj), request.user)


#: Eski nom — mavjud importlar buzilmasin.
IsRestaurantOwner = IsRestaurantMember
