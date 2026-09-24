"""Kim qaysi restoranni boshqara oladi.

Ilgari bitta `Restaurant.owner` tekshirilardi. Endi restoranda bir nechta
a'zo bo'ladi (`RestaurantMember`), shuning uchun tekshiruv a'zolik bo'yicha.
Pul bilan bog'liq amallar (to'lov, tarif) va admin qo'shish esa faqat
`owner` rolidagi a'zoda qoladi.
"""

from rest_framework.permissions import BasePermission

from .models import Category, Dish, Restaurant, RestaurantMember


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


class IsRestaurantMember(BasePermission):
    """Faqat o'zi a'zo bo'lgan restoranning ma'lumotlari."""

    message = "Bu ma'lumot sizning restoraningizga tegishli emas."

    def has_object_permission(self, request, view, obj) -> bool:
        return is_member(restaurant_of(obj), request.user)


#: Eski nom — mavjud importlar buzilmasin.
IsRestaurantOwner = IsRestaurantMember
