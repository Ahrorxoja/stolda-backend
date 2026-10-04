"""Admin formasidagi AI tugmalari — kaloriya, tarkib, tavsif.

Hech biri hech narsa saqlamaydi: natija formaga taklif sifatida qaytadi,
egasi "Qo'llash" / "Saqlash" bosgandagina oddiy `PATCH /api/dishes/` ketadi.
Matnlar restoranning asosiy tilida yuboriladi va shu tilda qaytadi.
"""

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai import AiError, DishFacts, get_assistant

from .admin_serializers import DishAiSerializer, IngredientsAiSerializer
from .models import Category, Restaurant
from .permissions import member_restaurant_ids, require
from .translations import translate


class AiView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_scope = "ai"
    serializer_class = DishAiSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        restaurant = Restaurant.objects.filter(
            pk=data["restaurant"], pk__in=member_restaurant_ids(request.user)
        ).first()
        if restaurant is None:
            raise PermissionDenied("Restoran topilmadi.")
        require(restaurant, request.user, "dishes")

        try:
            return Response(self.run(data, restaurant))
        except AiError as error:
            return Response({"detail": str(error)}, status=status.HTTP_502_BAD_GATEWAY)

    def run(self, data: dict, restaurant: Restaurant) -> dict:
        raise NotImplementedError

    @staticmethod
    def facts(data: dict, restaurant: Restaurant) -> DishFacts:
        category = ""
        if data.get("category"):
            found = Category.objects.filter(pk=data["category"], restaurant=restaurant).first()
            if found:
                category = translate(found.name, restaurant.primary_language)
        return DishFacts(
            name=data["name"].strip(),
            lang=restaurant.primary_language,
            weight=data.get("weight"),
            unit=data["unit"],
            description=data.get("description", "").strip(),
            ingredients=[item.strip() for item in data.get("ingredients", []) if item.strip()],
            category=category,
        )


class KcalAiView(AiView):
    """`POST /api/ai/kcal/` → `{"kcal", "low", "high"}` — bir porsiya uchun taxmin."""

    def run(self, data, restaurant):
        if not data.get("weight"):
            raise ValidationError({"weight": "Avval og'irligini kiriting."})
        estimate = get_assistant().estimate_kcal(self.facts(data, restaurant))
        return {"kcal": estimate.kcal, "low": estimate.low, "high": estimate.high}


class DescriptionAiView(AiView):
    """`POST /api/ai/description/` → `{"variants": [...]}` — 1–3 ta qisqa tavsif."""

    def run(self, data, restaurant):
        return {"variants": get_assistant().describe(self.facts(data, restaurant))}


class IngredientsAiView(AiView):
    """`POST /api/ai/ingredients/` → `{"items": [...]}` — imlosi tuzatilgan, ajratilgan ro'yxat."""

    serializer_class = IngredientsAiSerializer

    def run(self, data, restaurant):
        items = [item.strip() for item in data["items"] if item.strip()]
        return {"items": get_assistant().fix_ingredients(items, restaurant.primary_language)}
