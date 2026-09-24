"""Testlar uchun kichik yordamchilar."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from menu.models import Category, Dish, Plan, Restaurant, Subscription


def make_plan(code: str = "standard", **kwargs) -> Plan:
    defaults = {
        "name": "Standart" if code == "standard" else "Pro",
        "price_month": 99_000 if code == "standard" else 0,
        "price_year": 990_000 if code == "standard" else 0,
        "features": {"dish_limit": None, "stats": True},
        "is_public": True,
    }
    plan, _ = Plan.objects.get_or_create(code=code, defaults={**defaults, **kwargs})
    return plan


def make_restaurant(**kwargs) -> Restaurant:
    owner = kwargs.pop("owner", None) or get_user_model().objects.create_user(
        username=kwargs.get("slug", "egasi"), password="parol12345"
    )
    # `None` — obunasiz restoran kerak bo'lganda beriladi (masalan trial-check testlari).
    subscription_status = kwargs.pop("subscription_status", Subscription.Status.ACTIVE)
    plan = kwargs.pop("plan", None) or make_plan()

    defaults = {
        "slug": "zamin",
        "name": "Zamin",
        "cuisine": {"uz": "Milliy oshxona", "ru": "", "en": ""},
        "address": {"uz": "Toshkent", "ru": "", "en": ""},
        "phone": "+998 90 123 45 67",
        "service_charge_percent": 20,
    }
    restaurant = Restaurant.objects.create(owner=owner, **{**defaults, **kwargs})

    if subscription_status is not None:
        Subscription.objects.create(
            restaurant=restaurant,
            plan=plan,
            status=subscription_status,
            current_period_end=timezone.now() + timedelta(days=30),
        )
    return restaurant


def make_category(restaurant: Restaurant, **kwargs) -> Category:
    defaults = {
        "name": {"uz": "Salatlar", "ru": "Салаты", "en": "Salads"},
        "subtitle": {"uz": "Yangi", "ru": "", "en": ""},
        "icon": "salad",
    }
    return Category.objects.create(restaurant=restaurant, **{**defaults, **kwargs})


def make_dish(category: Category, **kwargs) -> Dish:
    defaults = {
        "name": {"uz": "Sezar salati", "ru": "Салат Цезарь", "en": "Caesar salad"},
        "description": {"uz": "Tovuq va parmezan", "ru": "", "en": ""},
        "ingredients": {"uz": ["Tovuq", "Parmezan"], "ru": [], "en": []},
        "price": 45000,
        "weight": 320,
        "kcal": 410,
    }
    return Dish.objects.create(category=category, **{**defaults, **kwargs})
