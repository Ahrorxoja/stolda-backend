"""`data/menu-sample.json` dan namuna "Zamin" restoranini yaratadi.

Buyruq idempotent: mavjud namuna restoran o'chirilib, qaytadan yaratiladi.
"""

import json
import random
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from menu.models import (
    Category,
    Dish,
    DishPhoto,
    MenuView,
    Plan,
    Restaurant,
    Subscription,
    Table,
    ViewKind,
)
from menu import translation_sync as sync
from menu.phones import normalize_phone
from menu.translations import LANGUAGES

DATA_FILE = Path(settings.BASE_DIR) / "data" / "menu-sample.json"
FOOD_DIR = Path(settings.BASE_DIR) / "assets" / "food"

# Admin panelga telefon raqami bilan kiriladi, shuning uchun username — raqam.
DEMO_OWNER_PHONE = "+998 90 123 45 67"
DEMO_OWNER_PASSWORD = "demo12345"
TABLE_COUNT = 8
VIEW_DAYS = 30
#: Namuna menyu shu tillarda yozilgan.
LANGUAGES_IN_SAMPLE = ("uz", "ru", "en")


def mark_sample_as_manual(obj, primary: str, langs) -> None:
    """Namunadagi tarjimalar odam yozgan — AI ularni qayta yozmasligi kerak.

    Belgilamasak, `auto_translate` yoqilgani uchun saqlash bilanoq tarjima
    task'i ishga tushadi va tayyor ruscha/inglizcha matnni almashtirib yuboradi.
    """
    digest = sync.source_hash(obj, primary)
    obj.translation_meta = {
        lang: {"source": "manual", "source_hash": digest}
        for lang in langs
        if lang != primary
    }


def translations(ui: dict, key: str) -> dict[str, str]:
    """`ui` bo'limidan bitta maydonni uch tilda yig'adi."""
    return {lang: ui.get(lang, {}).get(key, "") for lang in LANGUAGES}


class Command(BaseCommand):
    help = "Namuna menyu bilan 'zamin' restoranini yaratadi."

    def handle(self, *args, **options):
        self.verbosity = options.get("verbosity", 1)

        if not DATA_FILE.exists():
            raise CommandError(f"Namuna fayl topilmadi: {DATA_FILE}")

        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        meta, ui = data["restaurant"], data["ui"]

        with transaction.atomic():
            owner = self._owner()
            Restaurant.objects.filter(slug=meta["slug"]).delete()

            restaurant = Restaurant.objects.create(
                owner=owner,
                slug=meta["slug"],
                name=meta["name"],
                cuisine=translations(ui, "cuisine"),
                address=translations(ui, "address"),
                hours=translations(ui, "hours"),
                phone=meta.get("phone", ""),
                instagram=meta.get("instagram", ""),
                service_charge_percent=meta.get("service_charge_percent", 0),
                trial_used_at=timezone.now(),
                languages=list(LANGUAGES_IN_SAMPLE),
                primary_language="uz",
                auto_translate=True,
            )
            mark_sample_as_manual(restaurant, "uz", LANGUAGES_IN_SAMPLE)
            restaurant.save(update_fields=["translation_meta"])
            Subscription.objects.create(
                restaurant=restaurant,
                plan=Plan.objects.get(code="standard"),
                status=Subscription.Status.ACTIVE,
                current_period_end=timezone.now() + timedelta(days=30),
            )

            categories = {}
            for position, item in enumerate(data["categories"]):
                category = Category(
                    restaurant=restaurant,
                    name=item["name"],
                    subtitle=item["sub"],
                    icon=item["icon"],
                    position=position,
                )
                self._attach_photo(category, item.get("photo"))
                mark_sample_as_manual(category, "uz", LANGUAGES_IN_SAMPLE)
                category.save()
                categories[item["id"]] = category

            positions: dict[str, int] = {}
            for item in data["dishes"]:
                category = categories[item["cat"]]
                position = positions.get(item["cat"], 0)
                positions[item["cat"]] = position + 1

                dish = Dish(
                    category=category,
                    name=item["name"],
                    description=item["desc"],
                    ingredients=item["ingr"],
                    price=item["price"],
                    weight=item.get("weight"),
                    unit=item.get("unit", "g"),
                    kcal=item.get("kkal"),
                    badges=[item["badge"]] if item.get("badge") else [],
                    position=position,
                )
                mark_sample_as_manual(dish, "uz", LANGUAGES_IN_SAMPLE)
                dish.save()
                self._attach_dish_photo(dish, item.get("img"))

            tables = [
                Table.objects.create(restaurant=restaurant, number=str(number))
                for number in range(1, TABLE_COUNT + 1)
            ]
            views = self._seed_views(restaurant, tables)

        self.report(
            self.style.SUCCESS(
                f"'{restaurant.slug}' tayyor: {len(categories)} kategoriya, "
                f"{restaurant.dish_count} taom, {TABLE_COUNT} stol, "
                f"{views} ta ko'rish hodisasi."
            )
        )

    def report(self, message: str) -> None:
        if self.verbosity:
            self.stdout.write(message)

    def _owner(self):
        User = get_user_model()
        phone = normalize_phone(DEMO_OWNER_PHONE)
        owner, created = User.objects.get_or_create(
            username=phone,
            defaults={
                "first_name": "Aziz",
                "last_name": "Rahimov",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            owner.set_password(DEMO_OWNER_PASSWORD)
            owner.save(update_fields=["password"])
            self.report(
                f"Egasi yaratildi: {phone} / {DEMO_OWNER_PASSWORD}"
            )
        return owner

    def _seed_views(self, restaurant: Restaurant, tables: list[Table]) -> int:
        """Admin paneldagi statistika bo'sh ko'rinmasligi uchun namuna ko'rishlar.

        Mashhur taomlar ko'proq ochilgan bo'ladi, dam olish kunlari bandroq.
        """
        rng = random.Random(2026)
        dishes = list(Dish.objects.filter(category__restaurant=restaurant))
        weights = [3 if dish.badges else 1 for dish in dishes]
        now = timezone.localtime()
        events = []

        for day in range(VIEW_DAYS):
            moment = now - timedelta(days=day)
            busy = 1.6 if moment.weekday() >= 5 else 1.0
            scans = int(rng.randint(18, 34) * busy)

            for _ in range(scans):
                created = moment.replace(
                    hour=rng.randint(11, 22), minute=rng.randint(0, 59)
                )
                events.append(
                    MenuView(
                        restaurant=restaurant,
                        table=rng.choice(tables),
                        kind=ViewKind.SCAN,
                        created_at=created,
                    )
                )
                for dish in rng.choices(dishes, weights=weights, k=rng.randint(1, 5)):
                    events.append(
                        MenuView(
                            restaurant=restaurant,
                            dish=dish,
                            table=None,
                            kind=ViewKind.DISH_OPEN,
                            created_at=created,
                        )
                    )

        # `created_at` da `auto_now_add` bor: bulk_create nafaqat bazaga hozirgi
        # vaqtni yozadi, balki obyektlardagi qiymatni ham almashtiradi. Shuning
        # uchun kerakli sanalarni oldindan saqlab, keyin qaytaramiz.
        stamps = [event.created_at for event in events]
        MenuView.objects.bulk_create(events, batch_size=2000)
        for event, stamp in zip(events, stamps):
            event.created_at = stamp
        MenuView.objects.bulk_update(events, ["created_at"], batch_size=1000)
        return len(events)

    def _attach_photo(self, obj, name: str | None) -> None:
        """Kategoriya muqovasi."""
        path = self._food_path(name)
        if path is None:
            return
        with path.open("rb") as handle:
            obj.photo.save(path.name, File(handle), save=False)

    def _attach_dish_photo(self, dish: Dish, name: str | None) -> None:
        """Taom rasmi — alohida `DishPhoto` yozuvi."""
        path = self._food_path(name)
        if path is None:
            return
        photo = DishPhoto(dish=dish, position=0)
        with path.open("rb") as handle:
            photo.image.save(path.name, File(handle), save=False)
        photo.save()

    def _food_path(self, name: str | None):
        if not name:
            return None
        path = FOOD_DIR / f"{name}.jpg"
        if not path.exists():
            self.report(self.style.WARNING(f"Rasm topilmadi: {path}"))
            return None
        return path
