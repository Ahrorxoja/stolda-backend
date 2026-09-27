"""Qidiruv tizimlari uchun menyular ro'yxati va band manzillar."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from menu.models import Subscription
from menu.slugs import RESERVED_SLUGS, unique_slug

from .factories import make_category, make_dish, make_restaurant


def owner(name: str):
    return get_user_model().objects.create_user(username=name, password="parol12345")


class PublicSitemapTests(TestCase):
    url = "/api/public/sitemap/"

    def slugs(self):
        return [row["slug"] for row in self.client.get(self.url).json()]

    def test_live_menu_with_dishes_is_listed(self):
        restaurant = make_restaurant()
        make_dish(make_category(restaurant))

        body = self.client.get(self.url).json()

        self.assertEqual(body[0]["slug"], restaurant.slug)
        self.assertEqual(body[0]["primary_language"], restaurant.primary_language)
        self.assertIn(restaurant.primary_language, body[0]["languages"])

    def test_empty_menu_is_not_listed(self):
        """Bo'sh menyu qidiruvda "yupqa sahifa" — zarar qiladi."""
        make_category(make_restaurant())

        self.assertEqual(self.slugs(), [])

    def test_hidden_dishes_only_are_not_listed(self):
        make_dish(make_category(make_restaurant()), is_available=False)

        self.assertEqual(self.slugs(), [])

    def test_hidden_category_does_not_count(self):
        make_dish(make_category(make_restaurant(), is_visible=False))

        self.assertEqual(self.slugs(), [])

    def test_suspended_menu_is_not_listed(self):
        restaurant = make_restaurant(subscription_status=Subscription.Status.SUSPENDED)
        make_dish(make_category(restaurant))

        self.assertEqual(self.slugs(), [])

    def test_inactive_restaurant_is_not_listed(self):
        restaurant = make_restaurant(is_active=False)
        make_dish(make_category(restaurant))

        self.assertEqual(self.slugs(), [])

    def test_each_restaurant_appears_once(self):
        restaurant = make_restaurant()
        for _ in range(3):
            make_dish(make_category(restaurant))

        self.assertEqual(self.slugs(), [restaurant.slug])

    def test_no_sign_in_needed(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_does_not_clash_with_a_restaurant_route(self):
        self.assertEqual(reverse("public-sitemap"), self.url)


class ReservedSlugTests(TestCase):
    def test_language_pages_are_reserved(self):
        for slug in ("uz", "ru", "en", "join", "sitemap", "robots"):
            with self.subTest(slug=slug):
                self.assertIn(slug, RESERVED_SLUGS)

    def test_restaurant_called_ru_gets_another_address(self):
        self.assertNotEqual(unique_slug("RU"), "ru")
