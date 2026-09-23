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
    TableViewSet,
    TranslatePreviewView,
)
from .auth import GoogleAuthView, PhoneTokenObtainView, SignupView
from .billing_views import (
    AutopayView,
    BillingView,
    CancelView,
    CardView,
    InvoiceReceiptView,
    SubscribeView,
    WebhookView,
)
from .views import PublicMenuView, PublicViewEventView

router = DefaultRouter()
router.register("restaurants", RestaurantViewSet, basename="restaurant")
router.register("categories", CategoryViewSet, basename="category")
router.register("dishes", DishViewSet, basename="dish")
router.register("tables", TableViewSet, basename="table")
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
    # To'lov va tarif
    path("billing/", BillingView.as_view(), name="billing"),
    path("billing/subscribe/", SubscribeView.as_view(), name="billing-subscribe"),
    path("billing/card/", CardView.as_view(), name="billing-card"),
    path("billing/autopay/", AutopayView.as_view(), name="billing-autopay"),
    path("billing/cancel/", CancelView.as_view(), name="billing-cancel"),
    path(
        "billing/invoices/<int:pk>/receipt/",
        InvoiceReceiptView.as_view(),
        name="billing-invoice-receipt",
    ),
    path("billing/webhook/<str:provider>/", WebhookView.as_view(), name="billing-webhook"),
    path("", include(router.urls)),
]
