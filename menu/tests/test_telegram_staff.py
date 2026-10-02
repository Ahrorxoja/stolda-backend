"""@Stoldabot va restoranlar Mini App'i: Telegram bilan kirish, xodimlar ruxsatlari, jurnal."""

import hashlib
import hmac
import json
import time
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from menu import tgbot
from menu.models import ActivityLog, Profile, RestaurantInvite, RestaurantMember
from telegrambot import FakeChatBot

from .factories import make_category, make_dish, make_restaurant

TOKEN = "555:stolda-test"


def init_data(user_id, **user) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": int(user_id), "first_name": "Dilnoza", **user}),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_BOT_USERNAME="Stoldabot", SITE_URL="https://stolda.uz")
class StaffTestCase(TestCase):
    def setUp(self):
        self.bot = FakeChatBot()
        patcher = patch("menu.notify.get_restaurant_bot", return_value=self.bot)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.restaurant = make_restaurant(slug="zamin")
        self.owner = self.restaurant.owner
        self.category = make_category(self.restaurant)
        self.dish = make_dish(self.category, price=45_000)
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)

    def staff(self, permissions, telegram_id=None):
        user = get_user_model().objects.create_user(username=f"staff{RestaurantMember.objects.count()}", first_name="Aziz")
        RestaurantMember.objects.create(
            restaurant=self.restaurant, user=user, role=RestaurantMember.Role.MANAGER, permissions=permissions
        )
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def link_owner(self, telegram_id="700"):
        profile, _ = Profile.objects.get_or_create(user=self.owner)
        profile.telegram_id = telegram_id
        profile.save()

    def telegram(self, user_id, payload="", confirm=False):
        return APIClient().post(
            "/api/auth/telegram/", {"init_data": init_data(user_id), "payload": payload, "confirm": confirm}, format="json"
        )


class PermissionTests(StaffTestCase):
    def test_kitchen_staff_can_only_toggle_stoplist(self):
        _, client = self.staff(["stoplist"])
        url = f"/api/dishes/{self.dish.pk}/"

        self.assertEqual(client.patch(url, {"is_available": False}, format="json").status_code, 200)
        self.assertEqual(client.patch(url, {"price": 50_000}, format="json").status_code, 403)
        self.assertEqual(client.patch(url, {"name": {"uz": "Yangi"}}, format="json").status_code, 403)
        self.assertEqual(client.delete(url).status_code, 403)
        self.assertEqual(client.get("/api/stats/").status_code, 403)

    def test_price_permission_and_unchanged_fields_do_not_need_more(self):
        _, client = self.staff(["prices"])
        response = client.patch(
            f"/api/dishes/{self.dish.pk}/", {"price": 50_000, "is_available": True}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_only_owner_deletes_dishes_and_edits_settings(self):
        _, client = self.staff(list(RestaurantMember._meta.get_field("permissions").default()))
        self.assertEqual(client.delete(f"/api/dishes/{self.dish.pk}/").status_code, 403)
        self.assertEqual(
            client.patch(f"/api/restaurants/{self.restaurant.pk}/", {"name": "Boshqa"}, format="json").status_code, 403
        )
        self.assertEqual(
            client.patch(f"/api/restaurants/{self.restaurant.pk}/", {"qr_color": "#231C17"}, format="json").status_code,
            200,
        )
        self.assertEqual(self.owner_client.delete(f"/api/dishes/{self.dish.pk}/").status_code, 204)

    def test_me_lists_permissions(self):
        _, client = self.staff(["stoplist", "stats"])
        me = client.get("/api/me/").json()
        self.assertEqual(me["permissions"][str(self.restaurant.pk)], ["stoplist", "stats"])
        self.assertEqual(len(self.owner_client.get("/api/me/").json()["permissions"][str(self.restaurant.pk)]), 6)


class ActivityTests(StaffTestCase):
    def test_staff_price_change_is_logged_and_owner_can_undo_from_telegram(self):
        self.link_owner("700")
        _, client = self.staff(["prices"])
        with self.captureOnCommitCallbacks(execute=True):
            client.patch(f"/api/dishes/{self.dish.pk}/", {"price": 50_000}, format="json")

        entry = ActivityLog.objects.get()
        self.assertEqual((entry.action, entry.actor), ("price_changed", "Aziz"))
        self.assertIn("45 000 so'm → 50 000 so'm", entry.detail)
        message = self.bot.sent[-1]
        self.assertEqual(message["chat_id"], "700")
        self.assertEqual(message["buttons"], [("↩️ Qaytarish", f"undo:{entry.pk}")])

        tgbot.handle_undo(
            self.bot,
            {"id": "cb", "from": {"id": 700}, "data": f"undo:{entry.pk}", "message": {"message_id": 1, "chat": {"id": 700}, "text": "x"}},
        )
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.price, 45_000)
        self.assertIn("Qaytarildi", self.bot.answered[-1]["text"])

    def test_stranger_cannot_undo_and_owner_changes_are_silent(self):
        self.link_owner("700")
        with self.captureOnCommitCallbacks(execute=True):
            self.owner_client.patch(f"/api/dishes/{self.dish.pk}/", {"price": 60_000}, format="json")
        self.assertEqual(self.bot.sent, [])  # egasining o'zi — xabar yo'q
        entry = ActivityLog.objects.get()

        tgbot.handle_undo(self.bot, {"id": "cb", "from": {"id": 999}, "data": f"undo:{entry.pk}", "message": {}})
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.price, 60_000)

    def test_activity_endpoint_is_owner_only(self):
        _, client = self.staff(["stoplist"])
        client.patch(f"/api/dishes/{self.dish.pk}/", {"is_available": False}, format="json")

        self.assertEqual(client.get("/api/activity/").status_code, 403)
        rows = self.owner_client.get("/api/activity/").json()
        self.assertEqual(rows[0]["action"], "stock_off")


class TelegramAuthTests(StaffTestCase):
    def test_owner_links_with_one_time_link_after_confirming(self):
        url = self.owner_client.post("/api/telegram/link/").json()["url"]
        self.assertTrue(url.startswith("https://t.me/Stoldabot?start=L"))
        payload = url.split("start=")[1]

        preview = self.telegram(700, payload).json()
        self.assertEqual((preview["step"], preview["restaurant"]), ("confirm", "Zamin"))
        self.assertFalse(Profile.objects.filter(telegram_id="700").exists())

        done = self.telegram(700, payload, confirm=True).json()
        self.assertEqual(done["step"], "ok")
        self.assertEqual(Profile.objects.get(telegram_id="700").user, self.owner)
        # Bir martalik — ikkinchi marta ishlamaydi, lekin endi havolasiz kiradi.
        self.assertEqual(self.telegram(701, payload, confirm=True).status_code, 400)
        self.assertEqual(self.telegram(700).json()["step"], "ok")
        self.assertEqual(self.telegram(701).json()["step"], "unlinked")

    def test_bad_signature(self):
        response = APIClient().post("/api/auth/telegram/", {"init_data": "user=1&hash=bad"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_staff_joins_by_telegram_invite_with_chosen_permissions(self):
        response = self.owner_client.post(
            "/api/members/", {"channel": "telegram", "name": "Dilnoza", "permissions": ["stoplist", "prices"]}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()["url"].split("start=")[1]

        preview = self.telegram(800, payload).json()
        self.assertEqual(preview["kind"], "invite")
        self.assertEqual(len(preview["permissions"]), 2)
        tokens = self.telegram(800, payload, confirm=True).json()
        self.assertEqual(tokens["step"], "ok")

        member = RestaurantMember.objects.get(user__profile__telegram_id="800")
        self.assertEqual(member.permissions, ["stoplist", "prices"])
        self.assertTrue(RestaurantInvite.objects.get().accepted_at)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(client.patch(f"/api/dishes/{self.dish.pk}/", {"price": 1000}, format="json").status_code, 200)
        self.assertEqual(client.get("/api/stats/").status_code, 403)

    def test_owner_edits_permissions(self):
        user, client = self.staff(["stoplist"])
        member = RestaurantMember.objects.get(user=user)

        self.assertEqual(client.patch(f"/api/members/{member.pk}/", {"permissions": ["stats"]}, format="json").status_code, 403)
        response = self.owner_client.patch(f"/api/members/{member.pk}/", {"permissions": ["stats", "nope"]}, format="json")
        self.assertEqual(response.json()["permissions"], ["stats"])
        self.assertEqual(client.get("/api/stats/").status_code, 200)


class BotTests(StaffTestCase):
    def say(self, chat_id, text, chat_type="private"):
        tgbot.handle_message(self.bot, {"chat": {"id": chat_id, "type": chat_type}, "text": text})
        return self.bot.sent[-1] if self.bot.sent else None

    def test_start_for_unlinked_and_linked(self):
        self.assertIn("Telegram'ni ulash", self.say("123", "/start")["text"])
        self.link_owner("700")
        reply = self.say("700", "/start")
        self.assertIn("Zamin", reply["text"])
        self.assertEqual(reply["web_app"]["url"], "https://stolda.uz/app")

    def test_link_payload_opens_app_with_parameter(self):
        reply = self.say("700", "/start Labc123")
        self.assertEqual(reply["web_app"]["url"], "https://stolda.uz/app?p=Labc123")

    @override_settings(TELEGRAM_ADMIN_CHAT_ID="42")
    def test_platform_command_only_for_admin(self):
        self.say("700", "/platforma")
        self.assertEqual(self.bot.sent, [])
        self.assertIn("shu oy", self.say("42", "/platforma")["text"])

    @override_settings(TELEGRAM_ADMIN_CHAT_ID="42")
    def test_setup_sets_commands_and_menu_app(self):
        tgbot.setup(self.bot)
        self.assertIn(("platforma", "Platforma: shu oy raqamlari"), self.bot.commands["42"])
        self.assertNotIn("platforma", [name for name, _ in self.bot.commands[""]])
        self.assertEqual(self.bot.menu_app, ("Ilova", "https://stolda.uz/app"))


class TelegramAdoptionTests(StaffTestCase):
    def test_link_response_has_qr_image(self):
        data = self.owner_client.post("/api/telegram/link/").json()
        self.assertTrue(data["qr"].startswith("data:image/png;base64,"))

    def test_platform_lists_unlinked_restaurants(self):
        from menu import platform

        status = platform.telegram_status()
        self.assertEqual((status["total"], status["linked"]), (1, 0))
        self.assertEqual(status["unlinked"][0]["slug"], "zamin")

        self.link_owner("700")
        status = platform.telegram_status()
        self.assertEqual((status["linked"], status["unlinked"]), (1, []))

    def test_agents_get_weekly_list_of_unlinked_restaurants(self):
        from agents.models import Agent
        from agents.rules import RULES_VERSION
        from agents.services import remind_telegram
        from django.utils import timezone

        agent = Agent.objects.create(
            name="Ali", code="ALI", telegram_chat_id="55", rules_version=RULES_VERSION, rules_accepted_at=timezone.now()
        )
        self.restaurant.agent = agent
        self.restaurant.phone = "+998901112233"
        self.restaurant.save()
        agent_bot = FakeChatBot()
        with patch("agents.services.get_agent_bot", return_value=agent_bot), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(remind_telegram(), 1)
        self.assertIn("Zamin", agent_bot.sent[0]["text"])
        self.assertIn("+998 90 111 22 33", agent_bot.sent[0]["text"])


class BotTextsTests(StaffTestCase):
    def test_setup_sets_descriptions_in_three_languages_and_guide(self):
        tgbot.setup(self.bot)
        self.assertEqual(set(self.bot.descriptions), {"", "ru", "en"})
        tgbot.handle_message(self.bot, {"chat": {"id": "1", "type": "private"}, "text": "/yordam"})
        self.assertIn("Stop-list", self.bot.sent[-1]["text"])
