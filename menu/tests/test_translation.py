"""AI tarjima: task, qo'lda yozilgan tillar va preview endpoint."""

from datetime import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from menu import translation_sync as sync
from menu.models import Category, Dish, Restaurant
from menu.translations import SOURCE_AI, SOURCE_MANUAL
from translation import FakeTranslator, TranslationError

from .factories import make_category, make_dish, make_restaurant
from .test_admin_api import PASSWORD, AdminApiTestCase

NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


def languages(restaurant: Restaurant, codes: list[str], primary: str = "uz") -> Restaurant:
    restaurant.languages = codes
    restaurant.primary_language = primary
    restaurant.save(update_fields=["languages", "primary_language"])
    return restaurant


class TranslationSyncTests(TestCase):
    def setUp(self):
        self.restaurant = languages(make_restaurant(), ["uz", "ru", "en"])
        self.category = make_category(self.restaurant)

    def test_secondary_languages_exclude_the_primary_one(self):
        self.assertEqual(self.restaurant.secondary_languages, ["ru", "en"])

    def test_every_language_is_pending_before_the_first_translation(self):
        dish = make_dish(self.category)

        self.assertEqual(sorted(sync.pending_languages(dish)), ["en", "ru"])

    def test_manual_language_is_never_pending(self):
        dish = make_dish(self.category)
        dish.translation_meta = {"ru": {"source": SOURCE_MANUAL}}

        self.assertEqual(sync.pending_languages(dish), ["en"])

    def test_nothing_is_pending_when_the_text_has_not_changed(self):
        dish = make_dish(self.category)
        sync.apply_translations(
            dish, {"ru": {"name": "Долма"}, "en": {"name": "Dolma"}}, "uz"
        )

        self.assertEqual(sync.pending_languages(dish), [])

    def test_changing_the_primary_text_makes_it_pending_again(self):
        dish = make_dish(self.category)
        sync.apply_translations(dish, {"ru": {"name": "Долма"}}, "uz")
        dish.name = {**dish.name, "uz": "Boshqa nom"}

        self.assertIn("ru", sync.pending_languages(dish))

    def test_manual_text_is_reported_as_stale_but_left_alone(self):
        dish = make_dish(self.category)
        sync.mark_manual(dish, "ru", "uz")
        dish.name = {**dish.name, "uz": "Yangi nom"}

        self.assertEqual(sync.has_stale_manual(dish), ["ru"])
        self.assertNotIn("ru", sync.pending_languages(dish))

    def test_ingredient_lists_survive_the_round_trip(self):
        dish = make_dish(self.category)
        sync.apply_translations(dish, {"ru": {"ingredients": "Курица\nПармезан"}}, "uz")

        self.assertEqual(dish.ingredients["ru"], ["Курица", "Пармезан"])


class RetranslateTaskTests(TestCase):
    def setUp(self):
        self.restaurant = languages(make_restaurant(), ["uz", "ru", "en"])
        self.category = make_category(self.restaurant)

    def translate_now(self, dish):
        """Signal `on_commit` da ishlaydi — testda uni qo'lda ishga tushiramiz."""
        with self.captureOnCommitCallbacks(execute=True):
            dish.save()
        dish.refresh_from_db()
        return dish

    def test_saving_a_dish_fills_the_other_languages(self):
        dish = self.translate_now(make_dish(self.category))

        self.assertEqual(dish.name["ru"], "[ru] Sezar salati")
        self.assertEqual(dish.name["en"], "[en] Sezar salati")
        self.assertEqual(dish.translation_meta["ru"]["source"], SOURCE_AI)

    def test_manual_language_is_not_overwritten(self):
        dish = make_dish(self.category)
        dish.name = {**dish.name, "ru": "Qo'lda yozilgan"}
        sync.mark_manual(dish, "ru", "uz")
        dish = self.translate_now(dish)

        self.assertEqual(dish.name["ru"], "Qo'lda yozilgan")
        self.assertEqual(dish.name["en"], "[en] Sezar salati")

    def test_auto_translate_off_leaves_everything_alone(self):
        self.restaurant.auto_translate = False
        self.restaurant.save(update_fields=["auto_translate"])
        dish = self.translate_now(make_dish(self.category))

        # Fabrikadagi matn o'z holicha qoladi — AI unga tegmaydi.
        self.assertEqual(dish.name["ru"], "Салат Цезарь")
        self.assertEqual(dish.translation_meta, {})

    def test_restaurant_fields_are_translated_too(self):
        """Oshxona turi, manzil va ish vaqti ham tarjima qilinadi."""
        self.restaurant.cuisine = {"uz": "Milliy oshxona"}
        with self.captureOnCommitCallbacks(execute=True):
            self.restaurant.save()
        self.restaurant.refresh_from_db()

        self.assertEqual(self.restaurant.cuisine["ru"], "[ru] Milliy oshxona")
        self.assertEqual(self.restaurant.translation_meta["ru"]["source"], SOURCE_AI)

    def test_categories_are_translated_too(self):
        category = make_category(self.restaurant)
        with self.captureOnCommitCallbacks(execute=True):
            category.save()
        category.refresh_from_db()

        self.assertEqual(category.name["ru"], "[ru] Salatlar")

    def test_translation_does_not_loop(self):
        """Task obyektni saqlaydi — bu yana tarjimani chaqirmasligi kerak."""
        dish = self.translate_now(make_dish(self.category))

        with patch.object(
            FakeTranslator, "translate", side_effect=AssertionError("qayta chaqirildi")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                dish.save()

    def test_provider_failure_does_not_break_saving(self):
        with patch(
            "menu.tasks.get_translator",
            return_value=_Broken(),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                dish = make_dish(self.category)

        dish.refresh_from_db()
        self.assertEqual(dish.name["ru"], "Салат Цезарь")
        self.assertEqual(dish.translation_meta, {})


class _Broken:
    def translate(self, *args, **kwargs):
        raise TranslationError("provayder ishlamadi")


class LanguageRulesTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        languages(self.restaurant, ["uz", "ru", "en"])

    def test_lists_the_supported_languages(self):
        body = self.client.get("/api/restaurants/languages/", **self.auth()).json()

        codes = [row["code"] for row in body]
        self.assertIn("uz-Cyrl", codes)
        self.assertEqual(len(codes), 9)

    def test_primary_language_must_be_among_the_selected_ones(self):
        response = self.client.patch(
            f"/api/restaurants/{self.restaurant.pk}/",
            {"languages": ["uz", "ru"], "primary_language": "en"},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("primary_language", response.json())

    def test_language_list_cannot_be_empty(self):
        response = self.client.patch(
            f"/api/restaurants/{self.restaurant.pk}/",
            {"languages": []},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)

    def test_unknown_language_is_rejected(self):
        response = self.client.patch(
            f"/api/restaurants/{self.restaurant.pk}/",
            {"languages": ["uz", "xx"]},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)

    def test_adding_a_language_translates_the_whole_menu(self):
        category = make_category(self.restaurant)
        make_dish(category)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(
                f"/api/restaurants/{self.restaurant.pk}/",
                {"languages": ["uz", "ru", "en", "tr"]},
                content_type="application/json",
                **self.auth(),
            )

        self.assertEqual(Dish.objects.get(category=category).name["tr"], "[tr] Sezar salati")
        self.assertEqual(Category.objects.get(pk=category.pk).name["tr"], "[tr] Salatlar")


class TranslatePreviewTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        languages(self.restaurant, ["uz", "ru", "en"])
        self.url = reverse("translate-preview")

    def preview(self, payload):
        return self.client.post(
            self.url, payload, content_type="application/json", **self.auth()
        )

    def test_translates_without_saving(self):
        response = self.preview(
            {
                "restaurant": self.restaurant.pk,
                "targets": ["ru", "en"],
                "texts": {"name": "Do'lma", "ingredients": ["Uzum bargi", "Guruch"]},
            }
        )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["ru"]["name"], "[ru] Do'lma")
        self.assertEqual(body["ru"]["ingredients"], ["[ru] Uzum bargi", "Guruch"])

    def test_primary_language_is_skipped(self):
        body = self.preview(
            {
                "restaurant": self.restaurant.pk,
                "targets": ["uz"],
                "texts": {"name": "Do'lma"},
            }
        ).json()

        self.assertEqual(body, {})

    def test_language_outside_the_restaurant_is_skipped(self):
        body = self.preview(
            {
                "restaurant": self.restaurant.pk,
                "targets": ["de"],
                "texts": {"name": "Do'lma"},
            }
        ).json()

        self.assertEqual(body, {})

    def test_another_owners_restaurant_is_refused(self):
        other = make_restaurant(slug="boshqa", name="Boshqa")

        response = self.preview(
            {"restaurant": other.pk, "targets": ["ru"], "texts": {"name": "Do'lma"}}
        )

        self.assertEqual(response.status_code, 403)

    def test_requires_a_token(self):
        response = self.client.post(
            self.url,
            {"restaurant": self.restaurant.pk, "targets": ["ru"], "texts": {}},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)


@NO_CACHE
class CategoryScheduleTests(TestCase):
    """"Faqat belgilangan vaqtda" — kategoriya yashirinmaydi, belgilanadi.

    Menyu to'la ko'rinishi kerak: mijoz nima borligini ko'rib tursin va
    qachon berilishini `open_now` belgisidan bilsin. Faqat egasi qo'lda
    yashirgani (`is_visible=False`) menyudan butunlay chiqib ketadi.
    """

    def setUp(self):
        self.restaurant = make_restaurant()
        self.breakfast = make_category(
            self.restaurant, visible_from=time(8, 0), visible_to=time(11, 30)
        )
        make_dish(self.breakfast)
        self.url = reverse("public-menu", args=[self.restaurant.slug])

    def menu_at(self, hour: int, minute: int = 0):
        from django.utils import timezone

        moment = timezone.localtime().replace(hour=hour, minute=minute)
        with patch("menu.views.timezone.localtime", return_value=moment):
            return self.client.get(self.url).json()

    def test_inside_the_window_it_is_marked_open(self):
        category = self.menu_at(9)["categories"][0]

        self.assertTrue(category["open_now"])
        self.assertEqual(category["available_from"], "08:00")
        self.assertEqual(category["available_to"], "11:30")

    def test_before_the_window_it_still_shows_but_is_marked_closed(self):
        body = self.menu_at(7)

        self.assertEqual(len(body["categories"]), 1)
        self.assertFalse(body["categories"][0]["open_now"])

    def test_after_the_window_it_still_shows_but_is_marked_closed(self):
        body = self.menu_at(12)

        self.assertEqual(len(body["categories"]), 1)
        self.assertFalse(body["categories"][0]["open_now"])

    def test_its_dishes_stay_in_the_menu_outside_the_window(self):
        """Menyu to'la tursin — aks holda kechqurun menyu bo'sh ko'rinardi."""
        self.assertEqual(len(self.menu_at(12)["dishes"]), 1)

    def test_a_category_without_a_window_has_no_times_and_is_open(self):
        make_category(self.restaurant, position=1)

        plain = next(c for c in self.menu_at(12)["categories"] if c["position"] == 1)

        self.assertIsNone(plain["available_from"])
        self.assertIsNone(plain["available_to"])
        self.assertTrue(plain["open_now"])

    def test_window_may_cross_midnight(self):
        night = make_category(
            self.restaurant, visible_from=time(22, 0), visible_to=time(2, 0)
        )

        self.assertTrue(night.is_open_at(_at(23)))
        self.assertTrue(night.is_open_at(_at(1)))
        self.assertFalse(night.is_open_at(_at(12)))

    def test_hidden_category_never_shows(self):
        """Qo'lda yashirilgani boshqa gap — u umuman chiqmaydi."""
        self.breakfast.is_visible = False
        self.breakfast.save(update_fields=["is_visible"])

        body = self.menu_at(9)

        self.assertEqual(body["categories"], [])
        self.assertEqual(body["dishes"], [])


def _at(hour: int):
    from django.utils import timezone

    return timezone.localtime().replace(hour=hour, minute=0)


class ManualEditTests(AdminApiTestCase):
    """Admin qo'lda tahrirlasa, o'sha til `manual` bo'lib qoladi."""

    def setUp(self):
        super().setUp()
        languages(self.restaurant, ["uz", "ru", "en"])

    def test_manual_edit_survives_the_next_save(self):
        dish = make_dish(self.category)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(
                f"/api/dishes/{dish.pk}/",
                {
                    "name": {**dish.name, "ru": "Qo'lda yozilgan"},
                    "translation_meta": {"ru": {"source": "manual"}},
                },
                content_type="application/json",
                **self.auth(),
            )

        dish.refresh_from_db()
        self.assertEqual(dish.name["ru"], "Qo'lda yozilgan")
        self.assertEqual(dish.translation_meta["ru"]["source"], "manual")

    def test_manual_entry_gets_a_source_hash(self):
        dish = make_dish(self.category)

        self.client.patch(
            f"/api/dishes/{dish.pk}/",
            {"translation_meta": {"ru": {"source": "manual"}}},
            content_type="application/json",
            **self.auth(),
        )

        dish.refresh_from_db()
        self.assertTrue(dish.translation_meta["ru"]["source_hash"])
