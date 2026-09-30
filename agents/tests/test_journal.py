"""Borilgan joylar jurnali: yozish, takror, band, qidiruv, mijozlar bilan bog'lash."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from agents import bot as agent_bot
from agents import journal, services
from agents.models import Place, Visit
from menu.models import Restaurant, Subscription
from menu.tests.factories import make_restaurant
from telegrambot import FakeBot, FakeChatBot

from .test_agents import AgentTestCase, make_agent


class CleanLatinTests(TestCase):
    def test_accepts_latin_names_and_uzbek_apostrophes(self):
        self.assertEqual(journal.clean_latin("  Oqtepa   Lavash "), "Oqtepa Lavash")
        self.assertEqual(journal.clean_latin("Farg‘ona choyxonasi"), "Farg'ona choyxonasi")
        self.assertEqual(journal.clean_latin("Evos №12"), "Evos №12")

    def test_rejects_cyrillic_symbols_and_bad_length(self):
        for bad in ("Райхон", "A", "!!!", "12", "x" * 61):
            with self.subTest(bad=bad):
                self.assertEqual(journal.clean_latin(bad), "")


class JournalBotTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.bot = FakeChatBot()
        self.ali = make_agent(name="Ali Valiyev", code="ALI", telegram_chat_id="1")
        self.jasur = make_agent(name="Jasur Karimov", code="JASUR", telegram_chat_id="2")

    def say(self, agent, text):
        agent_bot.handle_message(self.bot, {"chat": {"id": agent.telegram_chat_id, "type": "private"}, "text": text})
        agent.refresh_from_db()
        return self.bot.sent[-1]

    def log_visit(self, agent, name="Rayhon", address="Chilonzor 9-kvartal", outcome=Visit.Outcome.INTERESTED, comment="Dushanba qaytaman"):
        self.say(agent, agent_bot.BTN_VISIT)
        self.say(agent, "Toshkent shahri")
        reply = self.say(agent, name)
        if agent.bot_state == agent_bot.STATE_VISIT_PICK:
            reply = self.say(agent, address if Place.objects.filter(address=address).exists() else agent_bot.BTN_NEW_ADDRESS)
            if agent.bot_state == agent_bot.STATE_VISIT_ADDRESS:
                self.say(agent, address)
        elif agent.bot_state == agent_bot.STATE_VISIT_ADDRESS:
            self.say(agent, address)
        self.say(agent, Visit.Outcome(outcome).label)
        return self.say(agent, comment)

    def test_logging_a_visit(self):
        reply = self.log_visit(self.ali)

        place = Place.objects.get()
        self.assertEqual((place.region, place.name, place.address), ("Toshkent shahri", "Rayhon", "Chilonzor 9-kvartal"))
        visit = place.visits.get()
        self.assertEqual((visit.agent, visit.outcome, visit.comment), (self.ali, "interested", "Dushanba qaytaman"))
        self.assertIn("Yozildi", reply["text"])
        self.assertIn("14 kun band", reply["text"])
        self.assertEqual(reply["keyboard"], agent_bot.KEYBOARD)
        self.assertEqual(self.ali.bot_state, "")

    def test_name_must_be_latin(self):
        self.say(self.ali, agent_bot.BTN_VISIT)
        self.say(self.ali, "Toshkent shahri")

        self.assertIn("lotin harflarida", self.say(self.ali, "Райхон")["text"])
        self.assertEqual(self.ali.bot_state, agent_bot.STATE_VISIT_NAME)

    def test_same_place_gets_history_and_second_agent_is_warned(self):
        self.log_visit(self.ali)

        self.say(self.jasur, agent_bot.BTN_VISIT)
        self.say(self.jasur, "toshkent shahri")
        pick = self.say(self.jasur, "rayhon")  # boshqa harf — o'sha joy
        self.assertIn("Chilonzor 9-kvartal", str(pick["keyboard"]))
        self.say(self.jasur, "Chilonzor 9-kvartal")
        warning = [item["text"] for item in self.bot.sent if "ishlayapti" in (item.get("text") or "")]
        self.assertTrue(warning and "Ali" in warning[-1])
        self.say(self.jasur, Visit.Outcome.REFUSED.label)
        self.say(self.jasur, agent_bot.BTN_SKIP)

        self.assertEqual(Place.objects.count(), 1)
        self.assertEqual(Place.objects.get().visits.count(), 2)

    def test_another_branch_is_a_separate_place(self):
        self.log_visit(self.ali)
        self.log_visit(self.jasur, address="Yunusobod 4-kvartal", outcome=Visit.Outcome.OTHER_QR)

        self.assertEqual(Place.objects.count(), 2)

    def test_cancel_and_main_buttons_leave_the_flow(self):
        self.say(self.ali, agent_bot.BTN_VISIT)
        self.assertIn("Bekor qilindi", self.say(self.ali, agent_bot.BTN_CANCEL)["text"])
        self.assertEqual(self.ali.bot_state, "")

        self.say(self.ali, agent_bot.BTN_VISIT)
        self.say(self.ali, "Toshkent shahri")
        self.assertIn("Balans", self.say(self.ali, agent_bot.BTN_BALANCE)["text"])
        self.assertEqual(self.ali.bot_state, "")

    def search(self, agent, query):
        self.say(agent, agent_bot.BTN_SEARCH)
        self.say(agent, "Toshkent shahri")
        return self.say(agent, query)["text"]

    def test_search_shows_history_comments_and_reservation(self):
        self.log_visit(self.ali)

        text = self.search(self.jasur, "rayh")

        self.assertIn("Rayhon", text)
        self.assertIn("Qiziqdi", text)
        self.assertIn("Dushanba qaytaman", text)
        self.assertIn("Ali bilan ishlanmoqda", text)
        self.assertNotIn("Valiyev", text)  # familiya ko'rinmaydi

    def test_search_all_and_nothing_found(self):
        self.log_visit(self.ali)
        self.assertIn("Rayhon", self.search(self.jasur, agent_bot.BTN_ALL))
        self.assertIn("hali hech kim bormagan", self.search(self.jasur, "zzzz"))

    def test_search_shows_existing_clients(self):
        make_restaurant(slug="rayhon-milliy", name="Rayhon Milliy", region="Toshkent shahri")
        make_restaurant(slug="rayhon-x", name="Rayhon X", region="")  # viloyati noma'lum
        make_restaurant(slug="rayhon-s", name="Rayhon S", region="Samarqand")  # boshqa viloyat

        text = self.search(self.jasur, "rayhon")

        self.assertIn("<b>Rayhon Milliy</b> — stolda.uz mijozi", text)
        self.assertIn("viloyati ko'rsatilmagan", text)
        self.assertNotIn("Rayhon S", text)

    def test_reservation_ends_after_fourteen_days(self):
        self.log_visit(self.ali)
        Visit.objects.update(created_at=timezone.now() - timedelta(days=journal.RESERVE_DAYS + 1))

        self.assertIsNone(journal.reservation(Place.objects.get()))
        self.assertNotIn("ishlanmoqda", self.search(self.jasur, "rayhon"))

    def test_other_outcomes_do_not_reserve(self):
        self.log_visit(self.ali, outcome=Visit.Outcome.REFUSED)

        self.assertIsNone(journal.reservation(Place.objects.get()))


@override_settings(TELEGRAM_ADMIN_CHAT_ID="4242")
class LinkRestaurantTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.ali = make_agent(name="Ali", code="ALI")
        self.jasur = make_agent(name="Jasur", code="JASUR")
        self.place = Place.objects.create(region="Toshkent shahri", name="Rayhon", address="Chilonzor")

    def signup(self, agent):
        restaurant = make_restaurant(
            slug="rayhon", name="Rayhon", region="Toshkent shahri", subscription_status=Subscription.Status.TRIALING
        )
        with self.captureOnCommitCallbacks(execute=True):
            services.attach(restaurant, agent)
        return restaurant

    def test_signup_marks_place_connected(self):
        Visit.objects.create(place=self.place, agent=self.ali, outcome=Visit.Outcome.INTERESTED)

        restaurant = self.signup(self.ali)

        self.place.refresh_from_db()
        self.assertEqual(self.place.restaurant, restaurant)
        self.assertEqual(self.place.visits.first().outcome, Visit.Outcome.CONNECTED)
        self.assertFalse(any("Band qilingan" in item.get("text", "") for item in self.owner_bot.sent))

    def test_owner_is_warned_when_someone_else_connects_a_reserved_place(self):
        Visit.objects.create(place=self.place, agent=self.ali, outcome=Visit.Outcome.INTERESTED)

        self.signup(self.jasur)

        warning = self.owner_bot.sent[-1]["text"]
        self.assertIn("Band qilingan joy boshqa agent orqali ulandi", warning)
        self.assertIn("ALI", warning)
        self.assertIn("JASUR", warning)
        self.assertEqual(Restaurant.objects.get().agent, self.jasur)  # daromad kod egasiga

    def test_restaurant_without_region_is_not_linked(self):
        restaurant = make_restaurant(slug="rayhon", name="Rayhon", subscription_status=Subscription.Status.TRIALING)
        services.attach(restaurant, self.ali)

        self.place.refresh_from_db()
        self.assertIsNone(self.place.restaurant)


class JournalListsTests(AgentTestCase):
    """«Borgan joylarim», «Barcha joylar», «Hamkorlarimiz»."""

    def setUp(self):
        super().setUp()
        self.bot = FakeChatBot()
        self.ali = make_agent(name="Ali Valiyev", code="ALI", telegram_chat_id="1")
        self.jasur = make_agent(name="Jasur Karimov", code="JASUR", telegram_chat_id="2")

    say = JournalBotTests.say
    log_visit = JournalBotTests.log_visit

    def test_my_visits_shows_only_my_places_with_last_result(self):
        self.log_visit(self.ali)
        self.log_visit(self.ali, outcome=Visit.Outcome.REFUSED, comment="Qimmat dedi")
        self.log_visit(self.jasur, name="Evos", address="Yunusobod")

        text = self.say(self.ali, agent_bot.BTN_MY_VISITS)["text"]

        self.assertIn("1 ta joy, 2 ta tashrif", text)
        self.assertIn("Rayhon", text)
        self.assertIn("Rad etdi", text)
        self.assertIn("Qimmat dedi", text)
        self.assertNotIn("Evos", text)

    def test_my_visits_empty(self):
        self.assertIn("Hali borgan joyingiz", self.say(self.ali, agent_bot.BTN_MY_VISITS)["text"])

    def test_all_places_in_region_from_every_agent(self):
        self.log_visit(self.ali)
        self.log_visit(self.jasur, name="Evos", address="Yunusobod", outcome=Visit.Outcome.OTHER_QR)

        self.say(self.ali, agent_bot.BTN_PLACES)
        text = self.say(self.ali, "Toshkent shahri")["text"]

        self.assertIn("2 ta joy", text)
        self.assertIn("Rayhon", text)
        self.assertIn("Evos", text)
        self.assertIn("Jasur", text)
        self.assertEqual(self.ali.bot_state, "")

        self.say(self.ali, agent_bot.BTN_PLACES)
        self.assertIn("hali hech kim", self.say(self.ali, "Samarqand")["text"])

    def test_partners_by_region_and_all(self):
        make_restaurant(slug="milliy", name="Milliy Taomlar", region="Toshkent shahri")
        make_restaurant(slug="sam", name="Registon Cafe", region="Samarqand")
        make_restaurant(slug="yopiq", name="Yopiq", region="Samarqand", subscription_status=Subscription.Status.SUSPENDED)
        make_restaurant(slug="namuna", name="Namuna", region="Samarqand", is_listed=False)

        self.say(self.ali, agent_bot.BTN_PARTNERS)
        text = self.say(self.ali, "Samarqand")["text"]
        self.assertIn("Registon Cafe", text)
        self.assertNotIn("Milliy", text)
        self.assertNotIn("Yopiq", text)
        self.assertNotIn("Namuna", text)

        self.say(self.ali, agent_bot.BTN_PARTNERS)
        text = self.say(self.ali, agent_bot.BTN_ALL_REGIONS)["text"]
        self.assertIn("(2)", text)
        self.assertIn("<b>Samarqand</b>", text)
        self.assertIn("Milliy Taomlar", text)

    def test_long_lists_are_split(self):
        messages = journal.chunks(["x" * 1000] * 10)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 3800 for message in messages))
