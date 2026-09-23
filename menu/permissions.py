from rest_framework.permissions import BasePermission

from .models import Category, Dish, Restaurant, Table


def owner_of(obj) -> int | None:
    """Obyekt qaysi foydalanuvchiga tegishli ekanini aniqlaydi."""
    if isinstance(obj, Restaurant):
        return obj.owner_id
    if isinstance(obj, (Category, Table)):
        return obj.restaurant.owner_id
    if isinstance(obj, Dish):
        return obj.category.restaurant.owner_id
    return None


class IsRestaurantOwner(BasePermission):
    """Faqat o'z restoranining ma'lumotlarini ko'rish va tahrirlash mumkin."""

    message = "Bu ma'lumot sizning restoraningizga tegishli emas."

    def has_object_permission(self, request, view, obj) -> bool:
        return owner_of(obj) == request.user.id
