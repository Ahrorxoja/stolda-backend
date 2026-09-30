from django.urls import path

from . import webapp
from .views import AgentCodeCheckView

urlpatterns = [
    path("check/", AgentCodeCheckView.as_view(), name="agent-check"),
    # Mini App (Telegram Web App) — `agents/webapp.py`.
    path("app/me/", webapp.MeView.as_view(), name="agent-app-me"),
    path("app/places/", webapp.PlacesView.as_view(), name="agent-app-places"),
    path("app/visits/", webapp.VisitCreateView.as_view(), name="agent-app-visits"),
    path("app/partners/", webapp.PartnersView.as_view(), name="agent-app-partners"),
    path("app/restaurants/", webapp.RestaurantsView.as_view(), name="agent-app-restaurants"),
    path("app/earnings/", webapp.EarningsView.as_view(), name="agent-app-earnings"),
    path("app/withdraw/", webapp.WithdrawView.as_view(), name="agent-app-withdraw"),
    path("app/card/", webapp.CardView.as_view(), name="agent-app-card"),
]
