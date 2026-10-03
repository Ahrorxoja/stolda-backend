"""`/api/billing/` — obuna, to'lov cheki va to'lovlar tarixi.

To'lov qo'lda: restoran egasi platforma kartasiga pul o'tkazadi, chekni
yuklaydi, platforma egasi Telegram'dan tasdiqlaydi.
"""

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import click

from .billing_serializers import (
    InvoiceSerializer,
    PaymentReceiptSerializer,
    PlanSerializer,
    ReceiptUploadSerializer,
    SubscriptionSerializer,
)
from .models import (
    ClickPayment,
    Invoice,
    PaymentReceipt,
    PlatformSettings,
    Plan,
    Restaurant,
    RestaurantMember,
    Subscription,
)
from .tasks import send_receipt_to_telegram


def _restaurant(request) -> Restaurant:
    """Joriy foydalanuvchining restorani.

    To'lov va tarif — faqat egasi uchun. Menejer menyuni boshqaradi, lekin
    pulga tegishli bo'limni ko'rmaydi (`RestaurantMember.Role.OWNER`).
    """
    restaurant = (
        Restaurant.objects.filter(
            members__user=request.user, members__role=RestaurantMember.Role.OWNER
        )
        .select_related("subscription", "subscription__plan")
        .first()
    )
    if restaurant is None:
        raise PermissionDenied("Bu bo'lim faqat restoran egasi uchun.")
    return restaurant


def _payment_details() -> dict:
    """Karta va yordam aloqalari — Django admindagi platforma sozlamalaridan.

    Maydon bo'sh bo'lsa `.env` dagi qiymatga qaytadi, shunda eski
    o'rnatmalar ham ishlayveradi.
    """
    platform = PlatformSettings.load()
    return {
        "card_number": platform.card_number
        or getattr(settings, "PAYMENT_CARD_NUMBER", ""),
        "card_holder": platform.card_holder
        or getattr(settings, "PAYMENT_CARD_HOLDER", ""),
        "support_phone": platform.support_phone,
        "support_telegram": platform.support_telegram,
    }


class BillingView(APIView):
    """`GET /api/billing/` — obuna, tariflar, to'lov ma'lumotlari, cheklar."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        restaurant = _restaurant(request)
        subscription = restaurant.subscription
        context = {"request": request}

        pending = subscription.receipts.filter(
            status=PaymentReceipt.Status.PENDING
        ).first()
        last_rejected = subscription.receipts.filter(
            status=PaymentReceipt.Status.REJECTED
        ).first()
        invoices = Invoice.objects.filter(subscription=subscription).select_related(
            "receipt"
        )[:12]

        return Response(
            {
                "subscription": SubscriptionSerializer(subscription).data,
                "plans": PlanSerializer(
                    Plan.objects.filter(is_public=True), many=True
                ).data,
                "payment": _payment_details(),
                "click_enabled": click.is_enabled(),
                "pending_receipt": (
                    PaymentReceiptSerializer(pending, context=context).data
                    if pending
                    else None
                ),
                "last_rejected_receipt": (
                    PaymentReceiptSerializer(last_rejected, context=context).data
                    if last_rejected
                    else None
                ),
                "invoices": InvoiceSerializer(invoices, many=True, context=context).data,
            }
        )


class ReceiptView(APIView):
    """`POST /api/billing/receipt/` — to'lov chekini yuklash (multipart).

    Summa tarif narxidan hisoblanadi; mijoz faqat davrni va rasmni yuboradi.
    """

    permission_classes = (IsAuthenticated,)
    throttle_scope = "receipt"

    def post(self, request):
        restaurant = _restaurant(request)
        subscription = restaurant.subscription

        if subscription.receipts.filter(status=PaymentReceipt.Status.PENDING).exists():
            return Response(
                {"detail": "Oldingi chek hali tekshirilmoqda — javobini kuting."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ReceiptUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        period = serializer.validated_data["period"]

        amount = subscription.plan.price_for(period)
        if amount <= 0:
            # Narxi qo'yilmagan davr (masalan Pro) — 0 so'mlik chek yozilmasin.
            return Response(
                {"detail": "Bu davr uchun to'lov hozircha mavjud emas."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        receipt = PaymentReceipt.objects.create(
            subscription=subscription,
            image=serializer.validated_data["image"],
            amount=amount,
            period=period,
        )
        transaction.on_commit(lambda: send_receipt_to_telegram.delay(receipt.pk))

        return Response(
            PaymentReceiptSerializer(receipt, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


#: To'lovdan keyin Click egasini qaytaradigan sahifalar.
CLICK_RETURN_PATHS = {"admin": "/admin/billing?click=done", "app": "/app"}


class ClickCreateView(APIView):
    """`POST /api/billing/click/` — Click orqali to'lash uchun havola.

    Mijoz faqat davrni yuboradi (`period`), summa tarifdan olinadi.
    `return_to`: `admin` (sukut) yoki `app` (Telegram ilova).
    """

    permission_classes = (IsAuthenticated,)
    throttle_scope = "receipt"

    def post(self, request):
        if not click.is_enabled():
            return Response(
                {"detail": "Click orqali to'lov hozircha ulanmagan."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        subscription = _restaurant(request).subscription
        period = request.data.get("period")
        if period not in Subscription.Period.values:
            return Response({"period": "Noto'g'ri davr."}, status=status.HTTP_400_BAD_REQUEST)
        amount = subscription.plan.price_for(period)
        if amount <= 0:
            return Response(
                {"detail": "Bu davr uchun to'lov hozircha mavjud emas."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = ClickPayment.objects.create(subscription=subscription, amount=amount, period=period)
        path = CLICK_RETURN_PATHS.get(request.data.get("return_to"), CLICK_RETURN_PATHS["admin"])
        return Response(
            {"id": payment.pk, "amount": amount, "url": click.pay_url(payment, settings.SITE_URL + path)},
            status=status.HTTP_201_CREATED,
        )


class _ClickCallbackView(APIView):
    """Click serveri chaqiradi — JWT yo'q, ishonch faqat imzoda."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    parser_classes = (FormParser, MultiPartParser, JSONParser)
    handler = None

    def post(self, request):
        return Response(type(self).handler(request.data))


class ClickPrepareView(_ClickCallbackView):
    """`POST /api/billing/click/prepare/`"""

    handler = staticmethod(click.prepare)


class ClickCompleteView(_ClickCallbackView):
    """`POST /api/billing/click/complete/`"""

    handler = staticmethod(click.complete)
