from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .admin_views import (
    CategoryViewSet,
    DishPhotoViewSet,
    DishViewSet,
    MeView,
    RestaurantViewSet,
    StatsView,
    TranslatePreviewView,
)
from .auth import GoogleAuthView, PhoneTokenObtainView, SignupView
from .billing_views import BillingView, ReceiptView
from .member_views import (
    InviteAcceptView,
    InviteDetailView,
    InvitePreviewView,
    MemberDetailView,
    MemberListView,
)
from .views import PublicMenuView, PublicViewEventView

router = DefaultRouter()
router.register("restaurants", RestaurantViewSet, basename="restaurant")
router.register("categories", CategoryViewSet, basename="category")
router.register("dishes", DishViewSet, basename="dish")
router.register("dish-photos", DishPhotoViewSet, basename="dish-photo")

urlpatterns = [
    # Public — QR skanerlagan mijoz uchun
    path("public/<slug:slug>/menu/", PublicMenuView.as_view(), name="public-menu"),
    path("public/<slug:slug>/views/", PublicViewEventView.as_view(), name="public-views"),
    # Admin — JWT
    path("auth/signup/", SignupView.as_view(), name="signup"),
    path("auth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("auth/token/", PhoneTokenObtainView.as_view(), name="token-obtain"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("stats/", StatsView.as_view(), name="stats"),
    path("translate/preview/", TranslatePreviewView.as_view(), name="translate-preview"),
    # To'lov va tarif — chek yuklash, Telegram'da tasdiqlanadi.
    path("billing/", BillingView.as_view(), name="billing"),
    path("billing/receipt/", ReceiptView.as_view(), name="billing-receipt"),
    # Xodimlar — restoranni bir nechta odam boshqarishi uchun.
    path("members/", MemberListView.as_view(), name="member-list"),
    path("members/<int:pk>/", MemberDetailView.as_view(), name="member-detail"),
    path("invites/<int:pk>/", InviteDetailView.as_view(), name="invite-detail"),
    path("invites/<str:token>/", InvitePreviewView.as_view(), name="invite-preview"),
    path("invites/<str:token>/accept/", InviteAcceptView.as_view(), name="invite-accept"),
    path("", include(router.urls)),
]
