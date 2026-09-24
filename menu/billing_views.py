"""`/api/billing/` — obuna, to'lov cheki va to'lovlar tarixi.

To'lov qo'lda: restoran egasi platforma kartasiga pul o'tkazadi, chekni
yuklaydi, platforma egasi Telegram'dan tasdiqlaydi.
"""

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .billing_serializers import (
    InvoiceSerializer,
    PaymentReceiptSerializer,
    PlanSerializer,
    ReceiptUploadSerializer,
    SubscriptionSerializer,
)
from .models import Invoice, PaymentReceipt, Plan, Restaurant, RestaurantMember
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
    """Mijozga ko'rsatiladigan karta — `.env` dan."""
    return {
        "card_number": getattr(settings, "PAYMENT_CARD_NUMBER", ""),
        "card_holder": getattr(settings, "PAYMENT_CARD_HOLDER", ""),
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

        plan = subscription.plan
        amount = plan.price_year if period == "year" else plan.price_month

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
