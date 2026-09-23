"""Public API serializerlari.

Menyu bitta so'rovda uchala tilda ham qaytadi — tilni mijoz brauzerda
almashtiradi, qayta so'rov yubormaydi.
"""

from rest_framework import serializers

from .models import Category, Dish, MenuView, Restaurant, Table, ViewKind


class ImageUrlMixin:
    def _image_url(self, image) -> str | None:
        if not image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(image.url) if request else image.url


class RestaurantSerializer(ImageUrlMixin, serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()
    cover = serializers.SerializerMethodField()

    class Meta:
        model = Restaurant
        fields = (
            "slug",
            "name",
            "languages",
            "primary_language",
            "cuisine",
            "address",
            "hours",
            "phone",
            "instagram",
            "logo",
            "cover",
            "service_charge_percent",
        )

    def get_logo(self, obj: Restaurant) -> str | None:
        return self._image_url(obj.logo)

    def get_cover(self, obj: Restaurant) -> str | None:
        return self._image_url(obj.cover)


class CategorySerializer(ImageUrlMixin, serializers.ModelSerializer):
    photo = serializers.SerializerMethodField()
    dish_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ("id", "name", "subtitle", "icon", "photo", "position", "dish_count")

    def get_photo(self, obj: Category) -> str | None:
        return self._image_url(obj.photo)

    def get_dish_count(self, obj: Category) -> int:
        # Odatda view'da `available_dishes` bo'lib prefetch qilinadi.
        prefetched = getattr(obj, "available_dishes", None)
        if prefetched is not None:
            return len(prefetched)
        return obj.dishes.filter(is_available=True).count()


class DishSerializer(ImageUrlMixin, serializers.ModelSerializer):
    #: `photo` — asosiy rasm (moslik uchun), `photos` — hammasi.
    photo = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    category = serializers.IntegerField(source="category_id")

    class Meta:
        model = Dish
        fields = (
            "id",
            "category",
            "name",
            "description",
            "ingredients",
            "price",
            "weight",
            "unit",
            "kcal",
            "photo",
            "photos",
            "badges",
            "position",
        )

    def get_photo(self, obj: Dish) -> str | None:
        photos = self._photo_list(obj)
        return photos[0] if photos else None

    def get_photos(self, obj: Dish) -> list[str]:
        return self._photo_list(obj)

    def _photo_list(self, obj: Dish) -> list[str]:
        # `gallery` view'da prefetch qilinadi.
        photos = getattr(obj, "gallery", None)
        if photos is None:
            photos = list(obj.photos.all())
        return [url for url in (self._image_url(p.image) for p in photos) if url]


class MenuViewCreateSerializer(serializers.Serializer):
    """`POST /api/public/{slug}/views/` uchun. Anonim, shaxsiy ma'lumot saqlanmaydi."""

    kind = serializers.ChoiceField(choices=ViewKind.choices)
    dish = serializers.IntegerField(required=False, allow_null=True)
    table = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def __init__(self, *args, restaurant: Restaurant, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def validate_dish(self, value):
        if value is None:
            return None
        dish = Dish.objects.filter(
            pk=value, category__restaurant=self.restaurant
        ).first()
        if dish is None:
            raise serializers.ValidationError("Bu restoranda bunday taom yo'q.")
        return dish

    def validate_table(self, value):
        if not value:
            return None
        # Noto'g'ri QR token hodisani yo'qotmaydi — shunchaki stol biriktirilmaydi.
        return Table.objects.filter(qr_token=value, restaurant=self.restaurant).first()

    def validate(self, attrs):
        if attrs.get("kind") == ViewKind.DISH_OPEN and not attrs.get("dish"):
            raise serializers.ValidationError(
                {"dish": "`dish_open` uchun taom ko'rsatilishi shart."}
            )
        return attrs

    def create(self, validated_data) -> MenuView:
        return MenuView.objects.create(
            restaurant=self.restaurant,
            dish=validated_data.get("dish"),
            table=validated_data.get("table"),
            kind=validated_data["kind"],
        )
