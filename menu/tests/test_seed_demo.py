import tempfile

from django.core.management import call_command
from django.test import TestCase, override_settings

from django.db.models.functions import TruncDate

from menu.models import Category, Dish, MenuView, Restaurant, ViewKind


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SeedDemoTests(TestCase):
    def test_creates_the_sample_restaurant(self):
        call_command("seed_demo", verbosity=0)

        restaurant = Restaurant.objects.get(slug="zamin")
        self.assertEqual(restaurant.service_charge_percent, 20)
        self.assertEqual(restaurant.cuisine["ru"], "Национальная и европейская кухня")
        self.assertEqual(Category.objects.count(), 6)
        self.assertEqual(Dish.objects.count(), 12)

    def test_dishes_keep_their_translations_and_badges(self):
        call_command("seed_demo", verbosity=0)

        dolma = Dish.objects.get(name__uz="Do'lma")
        self.assertEqual(dolma.price, 42000)
        self.assertEqual(dolma.badges, ["popular"])
        self.assertEqual(dolma.ingredients["en"][0], "Grape leaves")
        self.assertTrue(dolma.photo)

    def test_view_events_spread_across_many_days(self):
        """`auto_now_add` sabab hamma hodisa bugunga tushib qolmasligi kerak."""
        call_command("seed_demo", verbosity=0)

        days = (
            MenuView.objects.annotate(day=TruncDate("created_at"))
            .values("day")
            .distinct()
            .count()
        )

        self.assertGreater(days, 20)
        self.assertTrue(MenuView.objects.filter(kind=ViewKind.SCAN).exists())
        self.assertTrue(MenuView.objects.filter(kind=ViewKind.DISH_OPEN).exists())

    def test_running_twice_does_not_duplicate(self):
        call_command("seed_demo", verbosity=0)
        call_command("seed_demo", verbosity=0)

        self.assertEqual(Restaurant.objects.filter(slug="zamin").count(), 1)
        self.assertEqual(Dish.objects.count(), 12)

    def test_sample_translations_are_kept_as_hand_written(self):
        """Namunadagi ruscha matnni AI qayta yozib yubormasligi kerak."""
        call_command("seed_demo", verbosity=0)

        dish = Dish.objects.get(name__uz="Do'lma")
        category = Category.objects.get(name__uz="Issiq taomlar")

        self.assertEqual(dish.name["ru"], "Долма")
        self.assertEqual(category.name["ru"], "Горячие блюда")
        self.assertEqual(dish.translation_meta["ru"]["source"], "manual")
