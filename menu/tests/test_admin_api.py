import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.client import MULTIPART_CONTENT, encode_multipart
from django.urls import reverse
from PIL import Image

from menu.models import Category, Dish, Restaurant, Table
from menu.phones import normalize_phone

from .factories import make_category, make_dish, make_plan, make_restaurant

PASSWORD = "parol12345"

NO_CACHE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)


def png_bytes(name="rasm.png", size=(40, 30)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 150, 90)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class AdminApiTestCase(TestCase):
    """Telefon bilan kirib, `Authorization` sarlavhasi bilan so'rov yuboradi."""

    phone = "+998901112233"

    def setUp(self):
        self.restaurant = make_restaurant()
        self.owner = self.restaurant.owner
        self.owner.username = self.phone
        self.owner.set_password(PASSWORD)
        self.owner.save()
        self.category = make_category(self.restaurant)
        self.token = self.login(self.phone, PASSWORD)

    def login(self, phone: str, password: str) -> str | None:
        response = self.client.post(
            reverse("token-obtain"),
            {"phone": phone, "password": password},
            content_type="application/json",
        )
        if response.status_code != 200:
            return None
        return response.json()["access"]

    def auth(self, token: str | None = None) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {token or self.token}"}


class PhoneLoginTests(AdminApiTestCase):
    def test_accepts_the_number_in_any_format(self):
        for written in ("+998901112233", "998 90 111 22 33", "90 111 22 33"):
            with self.subTest(written=written):
                self.assertIsNotNone(self.login(written, PASSWORD))

    def test_rejects_a_wrong_password(self):
        self.assertIsNone(self.login(self.phone, "boshqa-parol"))

    def test_rejects_an_unknown_number(self):
        self.assertIsNone(self.login("+998900000000", PASSWORD))

    def test_me_lists_the_owners_restaurants(self):
        body = self.client.get(reverse("me"), **self.auth()).json()

        self.assertEqual(body["phone"], self.phone)
        self.assertEqual([r["slug"] for r in body["restaurants"]], ["zamin"])

    def test_me_requires_a_token(self):
        self.assertEqual(self.client.get(reverse("me")).status_code, 401)


class OwnerScopeTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.other = make_restaurant(slug="boshqa", name="Boshqa")
        self.other_category = make_category(self.other)
        self.other_dish = make_dish(self.other_category)

    def test_only_own_dishes_are_listed(self):
        make_dish(self.category)

        body = self.client.get("/api/dishes/", **self.auth()).json()

        self.assertEqual(len(body), 1)

    def test_another_owners_dish_is_not_found(self):
        response = self.client.get(f"/api/dishes/{self.other_dish.pk}/", **self.auth())

        self.assertEqual(response.status_code, 404)

    def test_cannot_move_a_dish_into_another_owners_category(self):
        response = self.client.post(
            "/api/dishes/",
            {
                "category": self.other_category.pk,
                "name": {"uz": "Test", "ru": "", "en": ""},
                "price": 1000,
            },
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.json())


class DishCrudTests(AdminApiTestCase):
    def test_creates_a_dish_with_three_languages(self):
        response = self.client.post(
            "/api/dishes/",
            {
                "category": self.category.pk,
                "name": {"uz": "Manti", "ru": "Манты", "en": "Manti"},
                "description": {"uz": "Bug'da", "ru": "", "en": ""},
                "ingredients": {"uz": ["Go'sht", "Xamir"], "ru": [], "en": []},
                "price": 38000,
                "weight": 400,
                "kcal": 620,
                "badges": ["popular"],
            },
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201, response.content)
        dish = Dish.objects.get(pk=response.json()["id"])
        self.assertEqual(dish.name["ru"], "Манты")
        self.assertEqual(dish.badges, ["popular"])

    def test_rejects_an_unknown_badge(self):
        response = self.client.post(
            "/api/dishes/",
            {
                "category": self.category.pk,
                "name": {"uz": "Test"},
                "price": 1000,
                "badges": ["spicy"],
            },
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)

    def test_hides_a_dish_with_patch(self):
        dish = make_dish(self.category)

        response = self.client.patch(
            f"/api/dishes/{dish.pk}/",
            {"is_available": False},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 200)
        dish.refresh_from_db()
        self.assertFalse(dish.is_available)

    def test_reorder_saves_drag_and_drop_positions(self):
        first, second, third = (make_dish(self.category) for _ in range(3))

        response = self.client.post(
            "/api/dishes/reorder/",
            [
                {"id": third.pk, "position": 0},
                {"id": first.pk, "position": 1},
                {"id": second.pk, "position": 2},
            ],
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(
                Dish.objects.filter(category=self.category)
                .order_by("position")
                .values_list("pk", flat=True)
            ),
            [third.pk, first.pk, second.pk],
        )

    def test_reorder_ignores_dishes_of_other_owners(self):
        other_dish = make_dish(make_category(make_restaurant(slug="boshqa", name="B")))

        response = self.client.post(
            "/api/dishes/reorder/",
            [{"id": other_dish.pk, "position": 9}],
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.json()["updated"], 0)
        other_dish.refresh_from_db()
        self.assertEqual(other_dish.position, 0)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhotoUploadTests(AdminApiTestCase):
    def upload_photo(self, dish, upload):
        return self.client.post(
            "/api/dish-photos/",
            encode_multipart("BoUnDaRy", {"dish": dish.pk, "image": upload}),
            content_type=MULTIPART_CONTENT.replace("BoUnDaRyStRiNg", "BoUnDaRy"),
            **self.auth(),
        )

    def test_uploaded_photo_is_stored_as_webp(self):
        dish = make_dish(self.category)

        response = self.upload_photo(dish, png_bytes())

        self.assertEqual(response.status_code, 201, response.content)
        photo = dish.photos.get()
        self.assertTrue(photo.image.name.endswith(".webp"))
        self.assertEqual(Image.open(photo.image.path).format, "WEBP")

    def test_large_photo_is_scaled_down(self):
        dish = make_dish(self.category)

        self.upload_photo(dish, png_bytes(size=(2400, 1800)))

        self.assertLessEqual(max(Image.open(dish.photos.get().image.path).size), 1600)


class PlanLimitApiTests(AdminApiTestCase):
    """Bepul tarif yo'q endi — mexanizm `subscription.plan.features` orqali ishlaydi."""

    def test_plan_with_a_dish_limit_blocks_further_creation(self):
        limited = make_plan(code="limited", features={"dish_limit": 30, "stats": False})
        self.restaurant.subscription.plan = limited
        self.restaurant.subscription.save(update_fields=["plan"])
        for _ in range(30):
            make_dish(self.category)

        response = self.client.post(
            "/api/dishes/",
            {"category": self.category.pk, "name": {"uz": "Ortiqcha"}, "price": 1000},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("30", str(response.json()))

    def test_plan_without_stats_blocks_the_stats_endpoint(self):
        limited = make_plan(code="limited", features={"dish_limit": None, "stats": False})
        self.restaurant.subscription.plan = limited
        self.restaurant.subscription.save(update_fields=["plan"])

        response = self.client.get(reverse("stats"), **self.auth())

        self.assertEqual(response.status_code, 403)

    def test_unlimited_plan_allows_many_dishes_and_stats(self):
        for _ in range(31):
            make_dish(self.category)

        self.assertTrue(self.restaurant.can_add_dish())
        self.assertTrue(self.restaurant.limits["stats"])


class StatsTests(AdminApiTestCase):
    def test_counts_scans_and_dish_opens(self):
        dish = make_dish(self.category)
        self.client.post(
            reverse("public-views", args=[self.restaurant.slug]),
            {"kind": "scan"},
            content_type="application/json",
        )
        for _ in range(3):
            self.client.post(
                reverse("public-views", args=[self.restaurant.slug]),
                {"kind": "dish_open", "dish": dish.pk},
                content_type="application/json",
            )

        body = self.client.get(reverse("stats") + "?range=week", **self.auth()).json()

        self.assertEqual(body["today"]["scans"], 1)
        self.assertEqual(body["today"]["dish_opens"], 3)
        self.assertEqual(body["top_dishes"][0]["views"], 3)
        self.assertEqual(len(body["daily"]), 7)

    def test_reports_hidden_dish_count(self):
        make_dish(self.category)
        make_dish(self.category, is_available=False)

        body = self.client.get(reverse("stats"), **self.auth()).json()

        self.assertEqual(body["dishes"], {"total": 2, "hidden": 1})


class TableQrTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.table = Table.objects.create(restaurant=self.restaurant, number="5")

    def test_downloads_a_png_for_one_table(self):
        response = self.client.get(f"/api/tables/{self.table.pk}/qr.png/", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_downloads_a_printable_pdf_for_every_table(self):
        response = self.client.get("/api/tables/qr.pdf/", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_is_not_found_when_there_are_no_tables(self):
        Table.objects.all().delete()

        self.assertEqual(self.client.get("/api/tables/qr.pdf/", **self.auth()).status_code, 404)


class MenuCacheTests(AdminApiTestCase):
    """Tahrirlangan menyu mijozga darhol ko'rinishi kerak."""

    def test_editing_a_dish_shows_up_immediately(self):
        dish = make_dish(self.category)
        url = reverse("public-menu", args=[self.restaurant.slug])
        self.assertEqual(len(self.client.get(url).json()["dishes"]), 1)

        self.client.patch(
            f"/api/dishes/{dish.pk}/",
            {"is_available": False},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(len(self.client.get(url).json()["dishes"]), 0)

    def test_reorder_also_refreshes_the_menu(self):
        first = make_dish(self.category, price=1000)
        second = make_dish(self.category, price=2000)
        url = reverse("public-menu", args=[self.restaurant.slug])
        self.assertEqual(
            [d["price"] for d in self.client.get(url).json()["dishes"]], [1000, 2000]
        )

        self.client.post(
            "/api/dishes/reorder/",
            [{"id": second.pk, "position": 0}, {"id": first.pk, "position": 1}],
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(
            [d["price"] for d in self.client.get(url).json()["dishes"]], [2000, 1000]
        )


class PhoneNormalisationTests(TestCase):
    def test_adds_the_country_code_to_a_nine_digit_number(self):
        self.assertEqual(normalize_phone("90 123 45 67"), "+998901234567")

    def test_keeps_a_full_number(self):
        self.assertEqual(normalize_phone("+998 90 123 45 67"), "+998901234567")

    def test_empty_input_gives_an_empty_string(self):
        self.assertEqual(normalize_phone(""), "")


class SignupTests(TestCase):
    """`POST /api/auth/signup/` — foydalanuvchi va restoran birga yaratiladi."""

    url = "/api/auth/signup/"

    def signup(self, **overrides):
        payload = {
            "phone": "+998 90 777 66 55",
            "password": "yangi-parol-2026",
            "restaurant_name": "Yangi Kafe",
            **overrides,
        }
        return self.client.post(self.url, payload, content_type="application/json")

    def test_creates_the_owner_and_the_restaurant(self):
        response = self.signup()

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIn("access", body)
        restaurant = Restaurant.objects.get(pk=body["restaurant"]["id"])
        self.assertEqual(restaurant.name, "Yangi Kafe")
        self.assertEqual(restaurant.slug, "yangi-kafe")
        self.assertEqual(restaurant.owner.username, "+998907776655")

    def test_the_returned_token_works(self):
        token = self.signup().json()["access"]

        response = self.client.get(
            reverse("me"), HTTP_AUTHORIZATION=f"Bearer {token}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["restaurants"]), 1)

    def test_slug_stays_unique(self):
        self.signup()
        second = self.signup(phone="+998901112200")

        self.assertEqual(second.json()["restaurant"]["slug"], "yangi-kafe-2")

    def test_rejects_a_duplicate_phone(self):
        self.signup()

        response = self.signup(restaurant_name="Boshqa")

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.json())

    def test_rejects_a_weak_password(self):
        response = self.signup(password="1234")

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json())

    def test_new_restaurant_starts_with_one_language(self):
        restaurant = Restaurant.objects.get(pk=self.signup().json()["restaurant"]["id"])

        self.assertEqual(restaurant.languages, ["uz"])
        self.assertEqual(restaurant.primary_language, "uz")


class CategoryDeleteTests(AdminApiTestCase):
    """Ichida taom bo'lgan kategoriya o'chirilmaydi."""

    def test_empty_category_can_be_deleted(self):
        response = self.client.delete(
            f"/api/categories/{self.category.pk}/", **self.auth()
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Category.objects.filter(pk=self.category.pk).exists())

    def test_category_with_dishes_is_refused(self):
        make_dish(self.category)

        response = self.client.delete(
            f"/api/categories/{self.category.pk}/", **self.auth()
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("taom", str(response.json()))
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_dishes_are_not_deleted_with_a_refused_category(self):
        dish = make_dish(self.category)

        self.client.delete(f"/api/categories/{self.category.pk}/", **self.auth())

        self.assertTrue(Dish.objects.filter(pk=dish.pk).exists())


class CategoryScheduleApiTests(AdminApiTestCase):
    def test_saves_a_visibility_window(self):
        response = self.client.patch(
            f"/api/categories/{self.category.pk}/",
            {"visible_from": "08:00", "visible_to": "11:30"},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.category.refresh_from_db()
        self.assertEqual(str(self.category.visible_from), "08:00:00")

    def test_both_ends_are_required(self):
        response = self.client.patch(
            f"/api/categories/{self.category.pk}/",
            {"visible_from": "08:00"},
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)

    def test_window_can_be_cleared(self):
        self.category.visible_from = "08:00"
        self.category.visible_to = "11:30"
        self.category.save()

        self.client.patch(
            f"/api/categories/{self.category.pk}/",
            {"visible_from": None, "visible_to": None},
            content_type="application/json",
            **self.auth(),
        )

        self.category.refresh_from_db()
        self.assertIsNone(self.category.visible_from)
