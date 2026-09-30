""""Kim nima qildi" jurnali va xodim o'zgarishlari haqida egasiga xabar.

Faqat menyudagi o'zgarishlar yoziladi (ko'rishlar emas). Xodim narxni
o'zgartirsa yoki taom qo'shsa — egasiga Telegram xabari; narxda "↩️ Qaytarish"
tugmasi bilan (`undo`), uni @Stoldabot callback'i chaqiradi.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import ActivityLog, Category, Dish, Restaurant
from .notify import notify_owners
from .permissions import is_owner
from .translations import translate

KEEP_DAYS = 90
A = ActivityLog.Action


def actor_name(user) -> str:
    profile = getattr(user, "profile", None)
    return (
        (profile.full_name if profile else "")
        or user.get_full_name()
        or user.first_name
        or user.get_username()
    )[:120]


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " so'm"


def dish_title(dish: Dish) -> str:
    restaurant = dish.category.restaurant
    return (translate(dish.name, restaurant.primary_language) or "—")[:160]


def category_title(category: Category) -> str:
    return (translate(category.name, category.restaurant.primary_language) or "—")[:160]


def log(restaurant: Restaurant, user, action: str, target: str, detail: str = "", data: dict | None = None) -> ActivityLog:
    entry = ActivityLog.objects.create(
        restaurant=restaurant,
        user=user,
        actor=actor_name(user),
        action=action,
        target=target,
        detail=detail,
        data=data or {},
    )
    if not is_owner(restaurant, user):
        _tell_owner(entry)
    return entry


def _tell_owner(entry: ActivityLog) -> None:
    """Xodim o'zgarishi — egasiga. Faqat muhimlari: narx va yangi taom."""
    if entry.action == A.PRICE_CHANGED:
        notify_owners(
            entry.restaurant,
            f"💰 <b>{entry.actor}</b> narxni o'zgartirdi\n{entry.target}: {entry.detail}",
            buttons=[("↩️ Qaytarish", f"undo:{entry.pk}")],
            changes_only=True,
        )
    elif entry.action == A.DISH_CREATED:
        notify_owners(
            entry.restaurant,
            f"🍽 <b>{entry.actor}</b> yangi taom qo'shdi: {entry.target}" + (f" — {entry.detail}" if entry.detail else ""),
            changes_only=True,
        )


def dish_saved(user, dish: Dish, before: dict | None) -> None:
    """Taom yaratildi (`before=None`) yoki o'zgardi — jurnalga bittadan qator."""
    restaurant = dish.category.restaurant
    title = dish_title(dish)
    if before is None:
        log(restaurant, user, A.DISH_CREATED, title, money(dish.price), {"dish": dish.pk})
        return
    if before["price"] != dish.price:
        log(
            restaurant,
            user,
            A.PRICE_CHANGED,
            title,
            f"{money(before['price'])} → {money(dish.price)}",
            {"dish": dish.pk, "old": before["price"], "new": dish.price},
        )
    if before["is_available"] != dish.is_available:
        log(restaurant, user, A.STOCK_ON if dish.is_available else A.STOCK_OFF, title, data={"dish": dish.pk})
    other = {key for key, value in before.items() if key not in ("price", "is_available", "position")}
    if any(before[key] != getattr(dish, key if key != "category" else "category_id") for key in other):
        log(restaurant, user, A.DISH_UPDATED, title, data={"dish": dish.pk})


def dish_snapshot(dish: Dish) -> dict:
    return {
        "price": dish.price,
        "is_available": dish.is_available,
        "position": dish.position,
        "name": dish.name,
        "description": dish.description,
        "ingredients": dish.ingredients,
        "weight": dish.weight,
        "unit": dish.unit,
        "kcal": dish.kcal,
        "badges": dish.badges,
        "category": dish.category_id,
    }


class UndoError(Exception):
    pass


@transaction.atomic
def undo(entry: ActivityLog, user) -> str:
    """Narx o'zgarishini qaytaradi. Faqat egasi; narx o'shandan beri o'zgarmagan bo'lsa."""
    entry = ActivityLog.objects.select_for_update().get(pk=entry.pk)
    if not is_owner(entry.restaurant, user):
        raise UndoError("Faqat restoran egasi qaytara oladi.")
    if entry.action != A.PRICE_CHANGED:
        raise UndoError("Bu o'zgarishni qaytarib bo'lmaydi.")
    if entry.undone_at:
        raise UndoError("Allaqachon qaytarilgan.")
    dish = Dish.objects.select_related("category__restaurant").filter(pk=entry.data.get("dish")).first()
    if dish is None:
        raise UndoError("Taom topilmadi — o'chirilgan bo'lishi mumkin.")
    if dish.price != entry.data.get("new"):
        raise UndoError("Narx o'shandan beri yana o'zgargan — admin panelda tekshiring.")
    before = dish_snapshot(dish)
    dish.price = entry.data["old"]
    dish.save(update_fields=["price"])
    entry.undone_at = timezone.now()
    entry.save(update_fields=["undone_at"])
    dish_saved(user, dish, before)
    from .cache import bump_menu_version

    bump_menu_version(dish.category.restaurant.slug)
    return f"Qaytarildi: {entry.target} — {money(dish.price)}"


def prune() -> int:
    deleted, _ = ActivityLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=KEEP_DAYS)).delete()
    return deleted
