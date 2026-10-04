from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .admin_views import (
    CategoryViewSet,
    DishPhotoViewSet,
    DishViewSet,
    MeView,
    RestaurantViewSet,
    PlatformOverviewView,
    StatsView,
    TranslatePreviewView,
)
from .ai_views import DescriptionAiView, IngredientsAiView, KcalAiView
from .auth import GoogleAuthView, PhoneTokenObtainView, SignupView
from .billing_views import (
    BillingView,
    ClickCompleteView,
    ClickCreateView,
    ClickPrepareView,
    ReceiptView,
)
from .member_views import (
    InviteAcceptView,
    InviteDetailView,
    InvitePreviewView,
    MemberDetailView,
    MemberListView,
)
from agents import platform_views

from .telegram_views import ActivityUndoView, ActivityView, TelegramAuthView, TelegramLinkView
from .views import PublicMenuView, PublicSitemapView, PublicViewEventView

router = DefaultRouter()
router.register("restaurants", RestaurantViewSet, basename="restaurant")
router.register("categories", CategoryViewSet, basename="category")
router.register("dishes", DishViewSet, basename="dish")
router.register("dish-photos", DishPhotoViewSet, basename="dish-photo")

urlpatterns = [
    # Public — QR skanerlagan mijoz uchun
    path("public/sitemap/", PublicSitemapView.as_view(), name="public-sitemap"),
    path("public/<slug:slug>/menu/", PublicMenuView.as_view(), name="public-menu"),
    path("public/<slug:slug>/views/", PublicViewEventView.as_view(), name="public-views"),
    # Admin — JWT
    path("auth/signup/", SignupView.as_view(), name="signup"),
    path("auth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("auth/telegram/", TelegramAuthView.as_view(), name="telegram-auth"),
    path("telegram/link/", TelegramLinkView.as_view(), name="telegram-link"),
    path("activity/", ActivityView.as_view(), name="activity"),
    path("activity/<int:pk>/undo/", ActivityUndoView.as_view(), name="activity-undo"),
    path("auth/token/", PhoneTokenObtainView.as_view(), name="token-obtain"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("stats/", StatsView.as_view(), name="stats"),
    path("platform/overview/", PlatformOverviewView.as_view(), name="platform-overview"),
    path("platform/agents/", platform_views.AgentListView.as_view(), name="platform-agents"),
    path("platform/agents/<int:pk>/", platform_views.AgentDetailView.as_view(), name="platform-agent"),
    path("platform/agents/<int:pk>/active/", platform_views.AgentActiveView.as_view(), name="platform-agent-active"),
    path("platform/restaurants/", platform_views.RestaurantSearchView.as_view(), name="platform-restaurants"),
    path("platform/restaurants/<int:pk>/agent/", platform_views.RestaurantAgentView.as_view(), name="platform-restaurant-agent"),
    path("translate/preview/", TranslatePreviewView.as_view(), name="translate-preview"),
    # AI yordamchi — admin formasidagi "✨ AI" tugmalari, hech narsa saqlamaydi.
    path("ai/kcal/", KcalAiView.as_view(), name="ai-kcal"),
    path("ai/ingredients/", IngredientsAiView.as_view(), name="ai-ingredients"),
    path("ai/description/", DescriptionAiView.as_view(), name="ai-description"),
    # To'lov va tarif — chek yuklash, Telegram'da tasdiqlanadi.
    path("billing/", BillingView.as_view(), name="billing"),
    path("billing/receipt/", ReceiptView.as_view(), name="billing-receipt"),
    path("billing/click/", ClickCreateView.as_view(), name="billing-click"),
    path("billing/click/prepare/", ClickPrepareView.as_view(), name="click-prepare"),
    path("billing/click/complete/", ClickCompleteView.as_view(), name="click-complete"),
    # Xodimlar — restoranni bir nechta odam boshqarishi uchun.
    path("members/", MemberListView.as_view(), name="member-list"),
    path("members/<int:pk>/", MemberDetailView.as_view(), name="member-detail"),
    path("invites/<int:pk>/", InviteDetailView.as_view(), name="invite-detail"),
    path("invites/<str:token>/", InvitePreviewView.as_view(), name="invite-preview"),
    path("invites/<str:token>/accept/", InviteAcceptView.as_view(), name="invite-accept"),
    path("", include(router.urls)),
]
