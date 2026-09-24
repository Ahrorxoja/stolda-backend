"""`/api/billing/` uchun serializerlar."""

from rest_framework import serializers

from .admin_serializers import WebpImageField
from .models import Invoice, PaymentReceipt, Plan, Subscription
from .serializers import ImageUrlMixin


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ("code", "name", "price_month", "price_year", "features", "is_public")


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = (
            "plan",
            "status",
            "period",
            "trial_ends_at",
            "current_period_end",
            "grace_ends_at",
        )


class PaymentReceiptSerializer(ImageUrlMixin, serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = PaymentReceipt
        fields = (
            "id",
            "image_url",
            "amount",
            "period",
            "status",
            "note",
            "created_at",
            "reviewed_at",
        )

    def get_image_url(self, obj: PaymentReceipt) -> str | None:
        return self._image_url(obj.image)


class InvoiceSerializer(ImageUrlMixin, serializers.ModelSerializer):
    receipt_url = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = (
            "id",
            "amount",
            "period",
            "period_start",
            "period_end",
            "status",
            "paid_at",
            "receipt_url",
        )

    def get_receipt_url(self, obj: Invoice) -> str | None:
        return self._image_url(obj.receipt.image) if obj.receipt else None


class ReceiptUploadSerializer(serializers.Serializer):
    """Chek yuklash — summa serverda hisoblanadi, mijozdan olinmaydi."""

    period = serializers.ChoiceField(choices=Subscription.Period.choices)
    image = WebpImageField()
