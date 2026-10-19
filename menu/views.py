"""Public API — QR kodni skanerlagan mijoz uchun. Autentifikatsiya talab qilinmaydi."""

from django.core.cache import cache
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ai.client import AiError
from .ai_assistant import ask_ai_waiter
from .cache import MENU_TTL_SECONDS, inactive_menu_cache_key, menu_cache_key, schedule_flag_key
from .models import AiQueryLog, Dish, DishPhoto, Restaurant, Subscription
from .serializers import (
    CategorySerializer,
    DishSerializer,
    MenuViewCreateSerializer,
    RestaurantSerializer,
)


def _get_restaurant(slug: str) -> Restaurant:
    return get_object_or_404(
        Restaurant.objects.select_related("subscription"), slug=slug, is_active=True
    )


class PublicMenuView(APIView):
    """`GET /api/public/{slug}/menu/` — restoran, kategoriyalar va mavjud taomlar.

    Javob 60 soniya keshlanadi; menyu tahrirlanganda kesh versiyasi oshadi
    (`menu.signals`), shuning uchun o'zgarish darhol ko'rinadi.
    """

    permission_classes = (AllowAny,)

    def get(self, request, slug: str):
        restaurant = _get_restaurant(slug)

        subscription = getattr(restaurant, "subscription", None)
        if subscription is not None and subscription.status == Subscription.Status.SUSPENDED:
            return self._inactive_response(restaurant, request)

        now = timezone.localtime()
        key = menu_cache_key(slug, request.get_host(), self._time_bucket(restaurant, now))
        cached = cache.get(key)
        if cached is not None:
            return Response(cached)

        restaurant.prefetched_custom_links = list(restaurant.custom_links.filter(is_visible=True))

        available = (
            Dish.objects.filter(is_available=True)
            .order_by("position", "id")
            .prefetch_related(
                Prefetch("photos", queryset=DishPhoto.objects.all(), to_attr="gallery")
            )
        )
        # Vaqt chegarasi kategoriyani yashirmaydi — menyu to'la ko'rinsin,
        # mijoz esa nima qachon borligini belgidan biladi (`open_now`).
        # Faqat egasi qo'lda yashirgani (`is_visible=False`) chiqmaydi.
        categories = [
            category
            for category in restaurant.categories.prefetch_related(
                Prefetch("dishes", queryset=available, to_attr="available_dishes")
            )
            if category.is_visible
        ]
        dishes = [dish for category in categories for dish in category.available_dishes]

        context = {"request": request, "now": now}
        payload = {
            "restaurant": RestaurantSerializer(restaurant, context=context).data,
            "categories": CategorySerializer(categories, many=True, context=context).data,
            "dishes": DishSerializer(dishes, many=True, context=context).data,
        }
        cache.set(key, payload, MENU_TTL_SECONDS)
        return Response(payload)

    @staticmethod
    def _inactive_response(restaurant: Restaurant, request) -> Response:
        """Obuna to'xtatilgan — mijoz "menyu faol emas" sahifasini ko'radi.

        404 emas, HTTP 200: sahifa haqiqatda mavjud, faqat vaqtincha yopiq.
        """
        key = inactive_menu_cache_key(restaurant.slug)
        cached = cache.get(key)
        if cached is not None:
            return Response(cached)

        context = {"request": request}
        full = RestaurantSerializer(restaurant, context=context).data
        payload = {
            "active": False,
            "restaurant": {
                field: full[field]
                for field in (
                    "name",
                    "logo",
                    "phone",
                    "extra_phones",
                    "address",
                    "instagram",
                    "facebook",
                    "telegram",
                )
            },
        }
        cache.set(key, payload, MENU_TTL_SECONDS)
        return Response(payload)

    @staticmethod
    def _time_bucket(restaurant: Restaurant, now) -> str:
        """Vaqtga bog'liq kategoriya bo'lsa, kesh kalitiga daqiqa qo'shiladi.

        Aks holda 08:00 da ochiladigan kategoriya keshda 60 soniya kechikardi.
        Bayroqning o'zi ham keshlanadi (menyu versiyasiga bog'lab), shuning
        uchun har bir so'rovda qo'shimcha so'rov bo'lmaydi.
        """
        key = schedule_flag_key(restaurant.slug)
        has_schedule = cache.get(key)
        if has_schedule is None:
            has_schedule = restaurant.categories.filter(visible_from__isnull=False).exists()
            cache.set(key, has_schedule, MENU_TTL_SECONDS)
        return now.strftime("%H:%M") if has_schedule else ""


class PublicViewEventView(APIView):
    """`POST /api/public/{slug}/views/` — QR skaner yoki taom ochilgani."""

    permission_classes = (AllowAny,)
    #: Anonim va ochiq — bitta IP statistikani shishira olmasin.
    throttle_scope = "views"

    def post(self, request, slug: str):
        restaurant = _get_restaurant(slug)
        serializer = MenuViewCreateSerializer(
            data=request.data, restaurant=restaurant
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicSitemapView(APIView):
    """`GET /api/public/sitemap/` — qidiruv tizimlariga ko'rsatiladigan menyular.

    Faqat haqiqatan ochiladigan va bo'sh bo'lmagan menyular: restoran faol,
    obunasi to'xtatilmagan va kamida bitta ko'rinadigan taomi bor. Bo'sh yoki
    yopiq menyuni indekslatish qidiruvda "yupqa sahifa" bo'lib zarar qiladi.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request):
        restaurants = (
            Restaurant.objects.filter(
                is_active=True,
                is_listed=True,
                categories__is_visible=True,
                categories__dishes__is_available=True,
            )
            .exclude(subscription__status=Subscription.Status.SUSPENDED)
            .distinct()
            .order_by("slug")
        )
        return Response(
            [
                {
                    "slug": restaurant.slug,
                    "languages": restaurant.languages or [restaurant.primary_language],
                    "primary_language": restaurant.primary_language,
                }
                for restaurant in restaurants
            ]
        )


class PublicAiAssistantView(APIView):
    """`POST /api/public/{slug}/ai-assistant/` — QR menyudagi AI Ofitsiant bilan muloqot."""

    permission_classes = (AllowAny,)
    throttle_scope = "ai"

    def post(self, request, slug: str):
        restaurant = _get_restaurant(slug)
        user_message = str(request.data.get("message") or "").strip()
        if not user_message:
            return Response(
                {"error": "Message is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        history = request.data.get("history") or []
        lang = str(request.data.get("lang") or "uz").strip()

        available = (
            Dish.objects.filter(is_available=True)
            .order_by("position", "id")
        )
        categories = [
            cat
            for cat in restaurant.categories.prefetch_related(
                Prefetch("dishes", queryset=available, to_attr="available_dishes")
            )
            if cat.is_visible
        ]
        dishes = [dish for cat in categories for dish in cat.available_dishes]

        try:
            reply = ask_ai_waiter(
                restaurant=restaurant,
                categories=categories,
                dishes=dishes,
                user_message=user_message,
                history=history,
                lang=lang,
            )
            AiQueryLog.objects.create(
                restaurant=restaurant,
                query_text=user_message[:500],
                lang=lang,
            )
            return Response({"reply": reply})
        except AiError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

