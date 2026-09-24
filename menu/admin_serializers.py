"""Admin panel (JWT) uchun serializerlar."""

import re

from rest_framework import serializers

from .images import to_webp
from .models import Badge, Category, Dish, DishPhoto, Profile, Restaurant
from .permissions import is_member
from .phones import (
    MAX_EXTRA_PHONES,
    clean_phone_list,
    is_valid_phone,
    normalize_phone,
)
from .serializers import ImageUrlMixin
from .slugs import RESERVED_SLUGS, normalize_slug
from .translations import (
    LANGUAGE_NAMES,
    validate_languages,
    validate_translation,
    validate_translation_list,
    validate_translation_meta,
)


class WebpImageField(serializers.ImageField):
    """Yuklangan rasmni serverda WebP ga siqadi."""

    def to_internal_value(self, data):
        return super().to_internal_value(to_webp(data))


class TranslatedField(serializers.JSONField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        validate_translation(value)
        return value


class TranslatedListField(serializers.JSONField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        validate_translation_list(value)
        return value


class TranslationMetaField(serializers.JSONField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        validate_translation_meta(value)
        return value


class ManualHashMixin:
    """Qo'lda tahrirlangan til uchun asosiy matn xeshini to'ldiradi.

    Admin formasi `{"ru": {"source": "manual"}}` yuboradi — xeshni bu yerda
    hisoblaymiz, shunda keyinchalik asosiy matn o'zgarsa "tarjima eskirdi"
    deb ko'rsatish mumkin bo'ladi.
    """

    def _fill_manual_hashes(self, instance):
        from .translation_sync import restaurant_of, source_hash

        meta = instance.translation_meta or {}
        pending = [
            lang
            for lang, entry in meta.items()
            if entry.get("source") == "manual" and not entry.get("source_hash")
        ]
        if not pending:
            return instance

        restaurant = restaurant_of(instance)
        if restaurant is None:
            return instance

        digest = source_hash(instance, restaurant.primary_language)
        instance.translation_meta = {
            **meta,
            **{lang: {**meta[lang], "source_hash": digest} for lang in pending},
        }
        instance.save(update_fields=["translation_meta"])
        return instance

    def create(self, validated_data):
        return self._fill_manual_hashes(super().create(validated_data))

    def update(self, instance, validated_data):
        return self._fill_manual_hashes(super().update(instance, validated_data))


class RestaurantAdminSerializer(ImageUrlMixin, serializers.ModelSerializer):
    cuisine = TranslatedField(required=False)
    address = TranslatedField(required=False)
    languages = serializers.JSONField(required=False)
    #: Bo'sh qatorlar formadan kelishi mumkin — ular `validate_extra_phones`
    #: da tozalanadi, shuning uchun model tekshiruvi bu yerda ulanmaydi.
    extra_phones = serializers.ListField(
        child=serializers.CharField(allow_blank=True, max_length=32),
        required=False,
    )
    translation_meta = TranslationMetaField(required=False)
    logo = WebpImageField(required=False, allow_null=True, write_only=True)
    cover = WebpImageField(required=False, allow_null=True, write_only=True)
    logo_url = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    dish_count = serializers.IntegerField(read_only=True)
    limits = serializers.DictField(read_only=True)
    #: Moslik uchun — hozirgi Sidebar shu maydonni o'qiydi. To'liq obuna
    #: holati `/api/billing/` orqali keladi (7.5-bosqich shu yerni almashtiradi).
    plan = serializers.SerializerMethodField()

    class Meta:
        model = Restaurant
        fields = (
            "id",
            "slug",
            "name",
            "languages",
            "primary_language",
            "auto_translate",
            "cuisine",
            "address",
            "working_hours",
            "phone",
            "extra_phones",
            "city",
            "instagram",
            "facebook",
            "telegram",
            "logo",
            "cover",
            "logo_url",
            "cover_url",
            "service_charge_percent",
            "plan",
            "is_active",
            "translation_meta",
            "dish_count",
            "limits",
        )

    def get_logo_url(self, obj: Restaurant) -> str | None:
        return self._image_url(obj.logo)

    def get_cover_url(self, obj: Restaurant) -> str | None:
        return self._image_url(obj.cover)

    def get_plan(self, obj: Restaurant) -> str:
        subscription = getattr(obj, "subscription", None)
        return subscription.plan.code if subscription else "standard"

    def validate_languages(self, value):
        validate_languages(value)
        return value

    def validate_phone(self, value: str) -> str:
        if not (value or "").strip():
            return ""
        if not is_valid_phone(value):
            raise serializers.ValidationError(
                "Telefon raqamini to'liq yozing, masalan +998 90 123 45 67."
            )
        return normalize_phone(value)

    def validate_extra_phones(self, value) -> list[str]:
        """Bo'sh qatorlar tushadi, raqamlar bir ko'rinishga keltiriladi."""
        for item in value:
            if (item or "").strip() and not is_valid_phone(item):
                raise serializers.ValidationError(
                    f"Telefon raqamini to'liq yozing: {item}"
                )
        cleaned = clean_phone_list(value)
        if len(cleaned) > MAX_EXTRA_PHONES:
            raise serializers.ValidationError(
                f"Ko'pi bilan {MAX_EXTRA_PHONES} ta qo'shimcha raqam qo'shsa bo'ladi."
            )
        return cleaned

    def validate_slug(self, value: str) -> str:
        slug = normalize_slug(value)
        if not slug:
            raise serializers.ValidationError("Manzil noto'g'ri.")
        if slug in RESERVED_SLUGS:
            raise serializers.ValidationError("Bu manzil band, boshqasini tanlang.")
        return slug

    def validate(self, attrs):
        languages = attrs.get("languages", getattr(self.instance, "languages", None)) or []
        primary = attrs.get(
            "primary_language", getattr(self.instance, "primary_language", None)
        )
        if primary and languages and primary not in languages:
            raise serializers.ValidationError(
                {"primary_language": "Asosiy til tanlangan tillar ichida bo'lishi kerak."}
            )
        # Asosiy raqam qo'shimchalar orasida ikkinchi marta turmasin.
        if "extra_phones" in attrs:
            main = attrs.get("phone", getattr(self.instance, "phone", ""))
            attrs["extra_phones"] = clean_phone_list(attrs["extra_phones"], main)
        return attrs


class CategoryAdminSerializer(ManualHashMixin, ImageUrlMixin, serializers.ModelSerializer):
    name = TranslatedField()
    subtitle = TranslatedField(required=False)
    photo = WebpImageField(required=False, allow_null=True, write_only=True)
    #: Kesilmagan nusxa — mijoz rasmni bosganda shu ochiladi.
    photo_original = WebpImageField(required=False, allow_null=True, write_only=True)
    photo_url = serializers.SerializerMethodField()
    photo_original_url = serializers.SerializerMethodField()
    dish_count = serializers.SerializerMethodField()
    translation_meta = TranslationMetaField(required=False)

    class Meta:
        model = Category
        fields = (
            "id",
            "restaurant",
            "name",
            "subtitle",
            "icon",
            "photo",
            "photo_original",
            "photo_url",
            "photo_original_url",
            "position",
            "is_visible",
            "visible_from",
            "visible_to",
            "translation_meta",
            "dish_count",
        )

    def get_photo_url(self, obj: Category) -> str | None:
        return self._image_url(obj.photo)

    def get_photo_original_url(self, obj: Category) -> str | None:
        return self._image_url(obj.photo_original)

    def get_dish_count(self, obj: Category) -> int:
        return obj.dishes.count()

    def validate(self, attrs):
        start = attrs.get("visible_from", getattr(self.instance, "visible_from", None))
        end = attrs.get("visible_to", getattr(self.instance, "visible_to", None))
        if (start is None) != (end is None):
            raise serializers.ValidationError(
                {"visible_to": "Vaqt oralig'ining ikkala chegarasi ham kerak."}
            )
        return attrs

    def validate_restaurant(self, value: Restaurant) -> Restaurant:
        if not is_member(value, self.context["request"].user):
            raise serializers.ValidationError("Bu restoran sizga tegishli emas.")
        return value


class DishPhotoSerializer(ImageUrlMixin, serializers.ModelSerializer):
    image = WebpImageField(write_only=True)
    original = WebpImageField(required=False, allow_null=True, write_only=True)
    url = serializers.SerializerMethodField()
    original_url = serializers.SerializerMethodField()

    class Meta:
        model = DishPhoto
        fields = ("id", "dish", "image", "original", "url", "original_url", "position")

    def get_url(self, obj: DishPhoto) -> str | None:
        return self._image_url(obj.image)

    def get_original_url(self, obj: DishPhoto) -> str | None:
        return self._image_url(obj.original)

    def validate_dish(self, value: Dish) -> Dish:
        if not is_member(value.category.restaurant, self.context["request"].user):
            raise serializers.ValidationError("Bu taom sizga tegishli emas.")
        return value


class DishAdminSerializer(ManualHashMixin, ImageUrlMixin, serializers.ModelSerializer):
    name = TranslatedField()
    description = TranslatedField(required=False)
    ingredients = TranslatedListField(required=False)
    translation_meta = TranslationMetaField(required=False)
    photos = DishPhotoSerializer(many=True, read_only=True)
    #: Asosiy rasmni bitta so'rovda almashtirish uchun. `photo` — menyuda
    #: ko'rinadigan kesilgan nusxa, `photo_original` — mijoz bosganda
    #: ochiladigan kesilmagan asl nusxa.
    photo = WebpImageField(required=False, allow_null=True, write_only=True)
    photo_original = WebpImageField(required=False, allow_null=True, write_only=True)
    photo_url = serializers.SerializerMethodField()
    photo_original_url = serializers.SerializerMethodField()

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
            "photos",
            "photo",
            "photo_original",
            "photo_url",
            "photo_original_url",
            "badges",
            "is_available",
            "position",
            "translation_meta",
        )

    def get_photo_url(self, obj: Dish) -> str | None:
        return self._image_url(obj.photo)

    def get_photo_original_url(self, obj: Dish) -> str | None:
        first = obj.photos.first()
        return self._image_url(first.original) if first else None

    def create(self, validated_data):
        photo, original = self._pop_photo(validated_data)
        dish = super().create(validated_data)
        self._apply_photo(dish, photo, original)
        return dish

    def update(self, instance, validated_data):
        photo, original = self._pop_photo(validated_data)
        dish = super().update(instance, validated_data)
        self._apply_photo(dish, photo, original)
        return dish

    @staticmethod
    def _pop_photo(validated_data):
        return validated_data.pop("photo", None), validated_data.pop("photo_original", None)

    @staticmethod
    def _apply_photo(dish: Dish, photo, original) -> None:
        """Asosiy rasmni almashtiradi — `photo=null` bo'lsa o'chiradi.

        Taomda bir nechta rasm bo'lishi mumkin; bu yerda faqat birinchisi
        (menyuda ko'rinadigani) boshqariladi.
        """
        if photo is None:
            return

        first = dish.photos.first()
        if photo is False or photo == "":
            if first:
                first.delete()
            return

        if first is None:
            DishPhoto.objects.create(dish=dish, image=photo, original=original, position=0)
            return

        first.image = photo
        fields = ["image"]
        if original is not None:
            first.original = original
            fields.append("original")
        first.save(update_fields=fields)

    def validate_category(self, value: Category) -> Category:
        if not is_member(value.restaurant, self.context["request"].user):
            raise serializers.ValidationError("Bu kategoriya sizga tegishli emas.")
        return value

    def validate_badges(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Belgilar ro'yxat bo'lishi kerak.")
        unknown = set(value) - set(Badge.values)
        if unknown:
            raise serializers.ValidationError(
                f"Noma'lum belgilar: {', '.join(sorted(unknown))}."
            )
        return value

    def validate(self, attrs):
        # Tarif chegarasi faqat yangi taom qo'shilganda tekshiriladi.
        if self.instance is None:
            category = attrs.get("category")
            if category and not category.restaurant.can_add_dish():
                limit = category.restaurant.limits["dish_limit"]
                raise serializers.ValidationError(
                    {
                        "detail": f"Tarifingizda {limit} tagacha taom qo'shish mumkin. "
                        "Ko'proq taom uchun tarifni yangilang."
                    }
                )
        return attrs


class TranslatePreviewSerializer(serializers.Serializer):
    """`POST /api/translate/preview/` — saqlamasdan tarjima qilib ko'rish."""

    restaurant = serializers.IntegerField()
    targets = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    #: Qiymat matn yoki matnlar ro'yxati bo'lishi mumkin (tarkib uchun).
    texts = serializers.DictField()

    def validate_targets(self, value):
        unknown = [code for code in value if code not in LANGUAGE_NAMES]
        if unknown:
            raise serializers.ValidationError(
                f"Noma'lum tillar: {', '.join(sorted(unknown))}."
            )
        return value

    def validate_texts(self, value):
        for key, text in value.items():
            if isinstance(text, list):
                if not all(isinstance(item, str) for item in text):
                    raise serializers.ValidationError(f"`{key}` ro'yxati matnlardan iborat bo'lsin.")
            elif not isinstance(text, str):
                raise serializers.ValidationError(f"`{key}` matn yoki ro'yxat bo'lishi kerak.")
        return value


class PositionSerializer(serializers.Serializer):
    """Drag-and-drop tartibini saqlash uchun: `[{"id": 3, "position": 0}, …]`."""

    id = serializers.IntegerField()
    position = serializers.IntegerField(min_value=0)


#: Telegram foydalanuvchi nomi: 5–32 ta harf, raqam yoki pastki chiziq.
TELEGRAM_NAME = re.compile(r"[A-Za-z0-9_]{5,32}")


class ProfileSerializer(serializers.ModelSerializer):
    """Hisob egasining aloqa ma'lumotlari — `/api/me/` orqali o'qiladi va yoziladi."""

    class Meta:
        model = Profile
        fields = ("full_name", "contact_phone", "telegram")

    def validate_contact_phone(self, value: str) -> str:
        if not value.strip():
            return ""
        if not is_valid_phone(value):
            raise serializers.ValidationError(
                "Telefon raqamini to'liq yozing, masalan +998 90 123 45 67."
            )
        return normalize_phone(value)

    def validate_telegram(self, value: str) -> str:
        """`@nom`, `nom` yoki havola — hammasi `@nom` ko'rinishiga keladi."""
        raw = (value or "").strip()
        if not raw:
            return ""
        name = raw.removeprefix("https://").removeprefix("http://")
        name = name.removeprefix("t.me/").removeprefix("telegram.me/").lstrip("@")
        name = name.split("?")[0].strip("/")
        if not TELEGRAM_NAME.fullmatch(name):
            raise serializers.ValidationError(
                "Telegram nomi 5–32 ta harf, raqam yoki pastki chiziqdan iborat "
                "bo'lishi kerak, masalan @aziz_rahimov."
            )
        return f"@{name}"
