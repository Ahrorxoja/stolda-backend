"""Admin panelga kirish — telefon raqami va parol, yoki Google orqali JWT."""

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import Restaurant
from .phones import normalize_phone
from .slugs import unique_slug


class PhoneTokenObtainSerializer(TokenObtainPairSerializer):
    """`username` o'rniga `phone` maydonini qabul qiladi."""

    username_field = "phone"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop(TokenObtainPairSerializer.username_field, None)
        self.fields["phone"] = serializers.CharField(write_only=True)

    def validate(self, attrs):
        phone = normalize_phone(attrs.get("phone", ""))
        if not phone:
            raise serializers.ValidationError({"phone": "Telefon raqami noto'g'ri."})

        user = authenticate(
            request=self.context.get("request"),
            username=phone,
            password=attrs.get("password", ""),
        )
        if user is None or not user.is_active:
            raise serializers.ValidationError(
                "Telefon raqami yoki parol noto'g'ri.", code="authorization"
            )

        refresh = self.get_token(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }


class PhoneTokenObtainView(TokenObtainPairView):
    serializer_class = PhoneTokenObtainSerializer


def create_owner(phone: str, password: str, **extra):
    """Egani telefon raqami bilan yaratadi (`username` — normallashtirilgan raqam)."""
    return get_user_model().objects.create_user(
        username=normalize_phone(phone), password=password, **extra
    )


class SignupSerializer(serializers.Serializer):
    """Telefon, parol va restoran nomi — ro'yxatdan o'tish uchun shu yetarli."""

    phone = serializers.CharField()
    password = serializers.CharField(write_only=True)
    restaurant_name = serializers.CharField(max_length=120)

    def validate_phone(self, value: str) -> str:
        phone = normalize_phone(value)
        if len(phone) < 10:
            raise serializers.ValidationError("Telefon raqami to'liq emas.")
        if get_user_model().objects.filter(username=phone).exists():
            raise serializers.ValidationError("Bu raqam allaqachon ro'yxatdan o'tgan.")
        return phone

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages)) from error
        return value

    @transaction.atomic
    def create(self, validated_data) -> Restaurant:
        user = get_user_model().objects.create_user(
            username=validated_data["phone"], password=validated_data["password"]
        )
        name = validated_data["restaurant_name"].strip()
        return Restaurant.objects.create(owner=user, name=name, slug=unique_slug(name))


class SignupView(APIView):
    """`POST /api/auth/signup/` — foydalanuvchi va restoran yaratadi, token qaytaradi."""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        restaurant = serializer.save()

        refresh = RefreshToken.for_user(restaurant.owner)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "restaurant": {"id": restaurant.pk, "slug": restaurant.slug},
            },
            status=status.HTTP_201_CREATED,
        )


class GoogleAuthSerializer(serializers.Serializer):
    """Google Identity Services yuborgan ID token (`credential`)."""

    credential = serializers.CharField()

    def validate_credential(self, value: str) -> dict:
        if not settings.GOOGLE_CLIENT_ID:
            raise serializers.ValidationError(
                "Google bilan kirish sozlanmagan (GOOGLE_CLIENT_ID yo'q)."
            )
        try:
            payload = google_id_token.verify_oauth2_token(
                value, google_requests.Request(), audience=settings.GOOGLE_CLIENT_ID
            )
        except ValueError as error:
            raise serializers.ValidationError(f"Google tokeni yaroqsiz: {error}") from error

        if not payload.get("email_verified"):
            raise serializers.ValidationError("Google email tasdiqlanmagan.")
        return payload

    def create(self, validated_data):
        payload = validated_data["credential"]
        email = payload["email"].lower()
        user, created = get_user_model().objects.get_or_create(
            username=email, defaults={"email": email}
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user


class GoogleAuthView(APIView):
    """`POST /api/auth/google/` — `{"credential": "<google id token>"}`, JWT qaytaradi.

    Ro'yxatdan o'tish ham, kirish ham shu — birinchi marta bo'lsa foydalanuvchi
    shu yerda yaratiladi (parolsiz), restoran esa alohida `/signup` oqimida.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})
