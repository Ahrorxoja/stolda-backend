"""Ikkinchi admin: taklif, qabul qilish va ruxsatlar."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from menu.members import accept_pending_for, create_invite
from menu.models import RestaurantInvite, RestaurantMember

from .factories import make_category, make_dish, make_restaurant


def make_account(email: str):
    return get_user_model().objects.create_user(username=email, password="parol12345")


class MemberApiTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.owner = self.restaurant.owner
        self.category = make_category(self.restaurant)
        self.dish = make_dish(self.category)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_existing_restaurant_has_an_owner_member(self):
        member = RestaurantMember.objects.get(restaurant=self.restaurant)
        self.assertEqual(member.user, self.owner)
        self.assertTrue(member.is_owner)

    def test_owner_sees_members_and_role(self):
        response = self.client.get("/api/members/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["role"], "owner")
        self.assertEqual(len(response.data["members"]), 1)
        self.assertTrue(response.data["members"][0]["is_you"])

    def test_owner_invites_by_email_and_gets_a_link(self):
        response = self.client.post("/api/members/", {"email": "Menejer@Gmail.com"})

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["email"], "menejer@gmail.com")
        self.assertIn("/join/", response.data["url"])

    def test_same_email_twice_replaces_the_pending_invite(self):
        self.client.post("/api/members/", {"email": "a@gmail.com"})
        self.client.post("/api/members/", {"email": "a@gmail.com"})

        self.assertEqual(
            RestaurantInvite.objects.filter(email="a@gmail.com").count(), 1
        )

    def test_owner_cancels_an_invite(self):
        invite = create_invite(self.restaurant, "a@gmail.com", self.owner)

        response = self.client.delete(f"/api/invites/{invite.pk}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(RestaurantInvite.objects.exists())

    def test_owner_cannot_remove_themselves(self):
        member = RestaurantMember.objects.get(restaurant=self.restaurant)

        response = self.client.delete(f"/api/members/{member.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(RestaurantMember.objects.filter(pk=member.pk).exists())


class InviteAcceptTests(TestCase):
    def setUp(self):
        self.restaurant = make_restaurant()
        self.owner = self.restaurant.owner
        self.invite = create_invite(self.restaurant, "menejer@gmail.com", self.owner)
        self.manager = make_account("menejer@gmail.com")
        self.client = APIClient()

    def test_preview_works_without_signing_in(self):
        response = self.client.get(f"/api/invites/{self.invite.token}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["restaurant"], self.restaurant.name)
        self.assertTrue(response.data["valid"])

    def test_manager_accepts_and_joins(self):
        self.client.force_authenticate(self.manager)

        response = self.client.post(f"/api/invites/{self.invite.token}/accept/")

        self.assertEqual(response.status_code, 200, response.data)
        member = RestaurantMember.objects.get(user=self.manager)
        self.assertEqual(member.restaurant, self.restaurant)
        self.assertEqual(member.role, "manager")

    def test_another_account_cannot_use_the_link(self):
        stranger = make_account("begona@gmail.com")
        self.client.force_authenticate(stranger)

        response = self.client.post(f"/api/invites/{self.invite.token}/accept/")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(RestaurantMember.objects.filter(user=stranger).exists())

    def test_expired_invite_is_refused(self):
        self.invite.expires_at = timezone.now() - timedelta(minutes=1)
        self.invite.save(update_fields=["expires_at"])
        self.client.force_authenticate(self.manager)

        response = self.client.post(f"/api/invites/{self.invite.token}/accept/")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(RestaurantMember.objects.filter(user=self.manager).exists())

    def test_link_works_only_once(self):
        self.client.force_authenticate(self.manager)
        self.client.post(f"/api/invites/{self.invite.token}/accept/")
        RestaurantMember.objects.filter(user=self.manager).delete()

        response = self.client.post(f"/api/invites/{self.invite.token}/accept/")

        self.assertEqual(response.status_code, 400)

    def test_signing_in_without_the_link_also_joins(self):
        """Havola yo'qolsa ham — pochta bo'yicha taklif topiladi."""
        member = accept_pending_for(self.manager)

        self.assertIsNotNone(member)
        self.assertEqual(member.restaurant, self.restaurant)

    def test_someone_with_a_restaurant_cannot_be_invited(self):
        other = make_restaurant(slug="boshqa", owner=make_account("band@gmail.com"))

        with self.assertRaises(Exception):
            create_invite(self.restaurant, "band@gmail.com", self.owner)
        self.assertEqual(
            RestaurantMember.objects.filter(restaurant=other).count(), 1
        )


class ManagerAccessTests(TestCase):
    """Menejer menyuni boshqaradi, to'lovni ko'rmaydi."""

    def setUp(self):
        self.restaurant = make_restaurant()
        self.owner = self.restaurant.owner
        self.category = make_category(self.restaurant)
        self.dish = make_dish(self.category)
        self.manager = make_account("menejer@gmail.com")
        RestaurantMember.objects.create(
            restaurant=self.restaurant,
            user=self.manager,
            role=RestaurantMember.Role.MANAGER,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_manager_sees_the_restaurant(self):
        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "manager")
        self.assertEqual(len(response.data["restaurants"]), 1)

    def test_manager_edits_the_menu(self):
        response = self.client.patch(
            f"/api/dishes/{self.dish.pk}/", {"price": 55000}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.price, 55000)

    def test_manager_creates_a_category(self):
        response = self.client.post(
            "/api/categories/",
            {"restaurant": self.restaurant.pk, "name": {"uz": "Yangi"}, "position": 5},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)

    def test_manager_cannot_open_billing(self):
        response = self.client.get("/api/billing/")

        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_invite(self):
        response = self.client.post("/api/members/", {"email": "yana@gmail.com"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(RestaurantInvite.objects.exists())

    def test_manager_cannot_remove_the_owner(self):
        owner_member = RestaurantMember.objects.get(user=self.owner)

        response = self.client.delete(f"/api/members/{owner_member.pk}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(RestaurantMember.objects.filter(pk=owner_member.pk).exists())

    def test_removed_manager_loses_access_at_once(self):
        RestaurantMember.objects.filter(user=self.manager).delete()

        response = self.client.get(f"/api/dishes/{self.dish.pk}/")

        self.assertEqual(response.status_code, 404)

    def test_manager_does_not_see_another_restaurants_dishes(self):
        other = make_restaurant(slug="boshqa", owner=make_account("ega2@gmail.com"))
        other_dish = make_dish(make_category(other))

        response = self.client.get(f"/api/dishes/{other_dish.pk}/")

        self.assertEqual(response.status_code, 404)
