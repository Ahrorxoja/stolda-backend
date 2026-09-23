"""Eski `Restaurant.plan` matnini haqiqiy `Plan`/`Subscription` qatorlariga ko'chiradi.

Bepul tarif olib tashlanmoqda: eski "free" restoranlar bugundan boshlab
14 kunlik sinov oladi (bu hozircha faqat dev/demo ma'lumotlari — production
mijozlari yo'q), eski "standard"/"pro" esa faol obunaga aylanadi.
"""

from datetime import timedelta

from django.db import migrations


def migrate_plans(apps, schema_editor):
    Restaurant = apps.get_model("menu", "Restaurant")
    Plan = apps.get_model("menu", "Plan")
    Subscription = apps.get_model("menu", "Subscription")

    from django.utils import timezone

    now = timezone.now()

    standard, _ = Plan.objects.get_or_create(
        code="standard",
        defaults={
            "name": "Standart",
            "price_month": 99_000,
            "price_year": 990_000,
            "features": {"dish_limit": None, "stats": True},
            "is_public": True,
        },
    )
    Plan.objects.get_or_create(
        code="pro",
        defaults={
            "name": "Pro",
            # Hozircha sotib bo'lmaydi ("tez orada") — narx aniqlanmagan.
            "price_month": 0,
            "price_year": 0,
            "features": {"dish_limit": None, "stats": True},
            "is_public": True,
        },
    )

    for restaurant in Restaurant.objects.all():
        if restaurant.plan == "standard":
            Subscription.objects.create(
                restaurant=restaurant,
                plan=standard,
                status="active",
                current_period_end=restaurant.created_at + timedelta(days=30),
            )
            restaurant.trial_used_at = restaurant.created_at
        else:
            # "free" yoki "pro" (hali sotilmagan) — bugundan yangi sinov.
            Subscription.objects.create(
                restaurant=restaurant,
                plan=standard,
                status="trialing",
                trial_ends_at=now + timedelta(days=14),
            )
            restaurant.trial_used_at = now
        restaurant.save(update_fields=["trial_used_at"])


def reverse(apps, schema_editor):
    Subscription = apps.get_model("menu", "Subscription")
    Plan = apps.get_model("menu", "Plan")
    Subscription.objects.all().delete()
    Plan.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("menu", "0005_billing_models"),
    ]

    operations = [
        migrations.RunPython(migrate_plans, reverse),
    ]
