"""`/api/billing/` — obuna, karta va to'lovlar tarixi."""

from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing import PaymentError, get_provider

from .billing_serializers import (
    AutopaySerializer,
    CardSerializer,
    InvoiceSerializer,
    PaymentMethodSerializer,
    PlanSerializer,
    SubscribeSerializer,
    SubscriptionSerializer,
)
from .models import Invoice, Plan, Restaurant


def _restaurant(request) -> Restaurant:
    """Joriy foydalanuvchining restorani — `StatsView._restaurant` bilan bir xil naqsh."""
    restaurant = (
        Restaurant.objects.filter(owner=request.user)
        .select_related("subscription", "subscription__plan")
        .first()
    )
    if restaurant is None:
        raise PermissionDenied("Restoran topilmadi.")
    return restaurant


class BillingView(APIView):
    """`GET /api/billing/` — obuna, tariflar, karta va oxirgi 12 hisob-faktura."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        restaurant = _restaurant(request)
        payment_method = restaurant.payment_methods.filter(is_default=True).first()
        invoices = Invoice.objects.filter(subscription=restaurant.subscription)[:12]

        return Response(
            {
                "subscription": SubscriptionSerializer(restaurant.subscription).data,
                "plans": PlanSerializer(
                    Plan.objects.filter(is_public=True), many=True
                ).data,
                "payment_method": (
                    PaymentMethodSerializer(payment_method).data if payment_method else None
                ),
                "invoices": InvoiceSerializer(invoices, many=True).data,
            }
        )


class SubscribeView(APIView):
    """`POST /api/billing/subscribe/` — `{plan, period, provider}`.

    Standart to'lov usuli bo'lsa darhol yechishga urinadi ("Hozir to'lash" /
    "To'lab tiklash"); bo'lmasa, provayderning to'lov sahifasiga havola
    qaytaradi ("Kartani biriktirish").
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = SubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        restaurant = _restaurant(request)
        subscription = restaurant.subscription
        subscription.period = data["period"]
        subscription.save(update_fields=["period"])

        plan = subscription.plan
        amount = plan.price_year if data["period"] == "year" else plan.price_month
        provider = get_provider(data["provider"])

        payment_method = restaurant.payment_methods.filter(
            is_default=True, provider=data["provider"]
        ).first()
        if payment_method is not None:
            try:
                result = provider.charge_token(
                    token=payment_method.token, amount=amount, description=plan.name
                )
            except PaymentError as error:
                return Response({"detail": str(error)}, status=status.HTTP_402_PAYMENT_REQUIRED)

            from .billing_ledger import record_payment

            record_payment(
                subscription,
                provider_payment_id=result.provider_payment_id,
                amount=amount,
                receipt_url=result.receipt_url,
            )
            return Response(SubscriptionSerializer(subscription).data)

        checkout_url = provider.create_checkout(
            amount=amount, description=plan.name, account={"restaurant_id": restaurant.pk}
        )
        return Response({"checkout_url": checkout_url})


class CardView(APIView):
    """`POST /api/billing/card/` — provayder tomonidan tokenlashtirilgan kartani biriktiradi.

    Karta raqamining o'zi bizga hech qachon kelmaydi — faqat token, oxirgi
    4 raqam va amal qilish muddati.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        restaurant = _restaurant(request)
        serializer = CardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        restaurant.payment_methods.update(is_default=False)
        payment_method = serializer.save(restaurant=restaurant, is_default=True)
        return Response(
            PaymentMethodSerializer(payment_method).data, status=status.HTTP_201_CREATED
        )


class AutopayView(APIView):
    """`PATCH /api/billing/autopay/` — `{autopay}`."""

    permission_classes = (IsAuthenticated,)

    def patch(self, request):
        serializer = AutopaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        subscription = _restaurant(request).subscription
        subscription.autopay = serializer.validated_data["autopay"]
        subscription.save(update_fields=["autopay"])
        return Response(SubscriptionSerializer(subscription).data)


class CancelView(APIView):
    """`POST /api/billing/cancel/` — avtomatik to'lovni bekor qiladi.

    Darhol to'xtatmaydi — joriy davr oxirigacha ishlayveradi, keyin kunlik
    vazifa (`process_subscriptions`) charge urinishini o'tkazib yuboradi.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        subscription = _restaurant(request).subscription
        subscription.canceled_at = timezone.now()
        subscription.autopay = False
        subscription.save(update_fields=["canceled_at", "autopay"])
        return Response(SubscriptionSerializer(subscription).data)


class InvoiceReceiptView(APIView):
    """`GET /api/billing/invoices/{id}/receipt/` — OFD fiskal chekka yo'naltiradi."""

    permission_classes = (IsAuthenticated,)

    def get(self, request, pk: int):
        invoice = Invoice.objects.filter(
            pk=pk, subscription__restaurant__owner=request.user
        ).first()
        if invoice is None or not invoice.receipt_url:
            raise NotFound("Chek topilmadi.")
        return redirect(invoice.receipt_url)


class WebhookView(APIView):
    """`POST /api/billing/webhook/{provider}/` — provayderdan, JWT'siz."""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def post(self, request, provider: str):
        try:
            handler = get_provider(provider)
        except ValueError:
            raise NotFound("Noma'lum to'lov provayderi.")
        return handler.handle_webhook(request)
