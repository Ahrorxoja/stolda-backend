"""Platforma egasi paneli: raqamlar to'g'ri hisoblanadimi va faqat egaga ko'rinadimi."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agents.models import Agent, AgentEarning, AgentWithdrawal, Place, Visit
from menu import platform
from menu.models import Invoice, Subscription
from menu.tasks import send_monthly_report
from telegrambot import FakeBot

from .factories import make_restaurant


def paid_invoice(restaurant, amount, when, period="month") -> Invoice:
    now = timezone.now()
    return Invoice.objects.create(
        subscription=restaurant.subscription,
        amount=amount,
        period=period,
        period_start=now,
        period_end=now + timedelta(days=30),
        status=Invoice.Status.PAID,
        paid_at=when,
        provider_payment_id=f"test_{Invoice.objects.count()}",
    )


class PlatformOverviewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="ega@gmail.com", is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.now = timezone.now()
        self.month = self.now.strftime("%Y-%m")

        self.monthly = make_restaurant(slug="oylik", region="Samarqand")
        self.yearly = make_restaurant(slug="yillik", region="Samarqand")
        Subscription.objects.filter(restaurant=self.yearly).update(period="year")
        self.trial = make_restaurant(slug="sinov", subscription_status=Subscription.Status.TRIALING)
        self.late = make_restaurant(slug="qarzdor", subscription_status=Subscription.Status.PAST_DUE)

        self.agent = Agent.objects.create(name="Ali Valiyev", code="ALI", telegram_chat_id="1")
        self.monthly.agent = self.agent
        self.monthly.save()

        first = paid_invoice(self.monthly, 99_000, self.now)
        paid_invoice(self.yearly, 990_000, self.now, period="year")
        paid_invoice(self.monthly, 99_000, self.now - timedelta(days=40))  # o'tgan oy
        AgentEarning.objects.create(
            agent=self.agent, restaurant=self.monthly, restaurant_name="Oylik", invoice=first, kind="first", amount=49_500
        )
        place = Place.objects.create(region="Samarqand", name="Rayhon", address="Markaz")
        Visit.objects.create(place=place, agent=self.agent, outcome="interested")

    def get(self, month=None):
        return self.client.get("/api/platform/overview/" + (f"?month={month}" if month else ""))

    def test_only_platform_owner(self):
        manager = get_user_model().objects.create_user(username="egasi2")
        client = APIClient()
        client.force_authenticate(manager)
        self.assertEqual(client.get("/api/platform/overview/").status_code, 403)
        self.assertEqual(APIClient().get("/api/platform/overview/").status_code, 401)
        self.assertEqual(self.get().status_code, 200)

    def test_money(self):
        money = self.get().json()["money"]

        self.assertEqual(money["revenue"], 99_000 + 990_000)
        self.assertEqual(money["payments"], 2)
        self.assertEqual(money["mrr"], 99_000 + 990_000 // 12)
        self.assertEqual(money["agent_share"], 49_500)
        self.assertEqual(money["agent_owed"], 49_500)
        self.assertEqual(money["net"], 99_000 + 990_000 - 49_500)

    def test_paid_withdrawal_moves_money_out_of_owed(self):
        withdrawal = AgentWithdrawal.objects.create(
            agent=self.agent, amount=49_500, card_number="8600", status="paid", processed_at=self.now
        )
        AgentEarning.objects.update(withdrawal=withdrawal)

        money = self.get().json()["money"]

        self.assertEqual(money["agent_paid"], 49_500)
        self.assertEqual(money["agent_owed"], 0)

    def test_series_covers_twelve_months(self):
        series = self.get().json()["series"]

        self.assertEqual(len(series), 12)
        self.assertEqual(series[-1]["month"], self.month)
        self.assertEqual(series[-1]["revenue"], 1_089_000)
        self.assertEqual(sum(row["revenue"] for row in series), 1_089_000 + 99_000)

    def test_restaurants_and_attention(self):
        data = self.get().json()

        restaurants = data["restaurants"]
        self.assertEqual(
            (restaurants["active"], restaurants["trialing"], restaurants["past_due"]), (2, 1, 1)
        )
        self.assertEqual(restaurants["new"], 4)
        self.assertEqual(restaurants["converted"], 1)  # "yillik" — birinchi to'lovi shu oy
        self.assertEqual(data["attention"]["past_due"], 1)
        samarqand = next(row for row in data["regions"] if row["region"] == "Samarqand")
        self.assertEqual((samarqand["total"], samarqand["paying"]), (2, 2))

    def test_agents_leaderboard(self):
        row = self.get().json()["agents"][0]

        self.assertEqual(row["name"], "Ali Valiyev")
        self.assertEqual((row["restaurants"], row["paying"], row["visits"]), (1, 1, 1))
        self.assertEqual((row["earned_month"], row["balance"]), (49_500, 49_500))

    def test_previous_month_and_bad_input(self):
        previous = self.now.replace(day=1) - timedelta(days=1)
        data = self.get(previous.strftime("%Y-%m")).json()
        self.assertEqual(data["money"]["revenue"], 99_000)
        self.assertEqual(self.get("2026-13").status_code, 400)
        self.assertEqual(self.get("salom").status_code, 400)

    def test_me_tells_who_is_the_platform_owner(self):
        self.assertTrue(self.client.get("/api/me/").json()["is_platform_owner"])

    def test_monthly_report_goes_to_telegram(self):
        text = platform.monthly_report_text(self.now.year, self.now.month)
        self.assertIn("1 089 000 so'm", text)
        self.assertIn("Ali Valiyev", text)

        bot = FakeBot()
        with patch("menu.tasks.get_bot", return_value=bot):
            send_monthly_report()
        self.assertIn("oylik hisobot", bot.sent[0]["text"])
