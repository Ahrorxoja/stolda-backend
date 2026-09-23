"""`/api/billing/` uchun serializerlar."""

from rest_framework import serializers

from .models import Invoice, PaymentMethod, Plan, Subscription


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
            "autopay",
            "canceled_at",
        )


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = (
            "id",
            "provider",
            "brand",
            "last4",
            "exp_month",
            "exp_year",
            "is_default",
        )


class InvoiceSerializer(serializers.ModelSerializer):
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


class SubscribeSerializer(serializers.Serializer):
    plan = serializers.ChoiceField(choices=["standard"])
    period = serializers.ChoiceField(choices=Subscription.Period.choices)
    provider = serializers.ChoiceField(choices=PaymentMethod.Provider.choices)


class CardSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ("provider", "token", "brand", "last4", "exp_month", "exp_year")


class AutopaySerializer(serializers.Serializer):
    autopay = serializers.BooleanField()
