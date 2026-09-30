"""Admin panel API — JWT bilan, faqat o'z restorani doirasida."""

import io
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from translation import TranslationError, get_translator

from .admin_serializers import (
    ProfileSerializer,
    CategoryAdminSerializer,
    DishAdminSerializer,
    DishPhotoSerializer,
    PositionSerializer,
    RestaurantAdminSerializer,
    TranslatePreviewSerializer,
)
from .models import (
    Category,
    Dish,
    DishPhoto,
    MenuView,
    Plan,
    Profile,
    Restaurant,
    RestaurantMember,
    Subscription,
    ViewKind,
)
from agents import services as agent_services

from . import platform
from . import qr as qr_codes
from . import qr_print
from .deletion import delete_restaurant, deletion_summary
from . import activity
from .permissions import (
    IsRestaurantMember,
    is_owner,
    member_restaurant_ids,
    permissions_of,
    require,
    require_owner,
)
from .serializers import absolute_media_url
from .phones import normalize_phone
from .slugs import RESERVED_SLUGS, normalize_slug
from .tasks import retranslate_restaurant
from .translation_sync import LIST_SEPARATOR
from .translations import LANGUAGE_NAMES, translate

RANGES = {"day": 1, "week": 7, "month": 30}


class OwnerScopedViewSet(viewsets.ModelViewSet):
    permission_classes = (IsAuthenticated, IsRestaurantMember)

    def reorder(self, request):
        """`POST .../reorder/` — `[{"id": …, "position": …}]`."""
        serializer = PositionSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        allowed = set(self.get_queryset().values_list("id", flat=True))
        rows = [row for row in serializer.validated_data if row["id"] in allowed]

        model = self.get_queryset().model
        objects = []
        for row in rows:
            obj = model(id=row["id"], position=row["position"])
            objects.append(obj)
        model.objects.bulk_update(objects, ["position"])

        # bulk_update signal yubormaydi — keshni o'zimiz yangilaymiz.
        self._invalidate()
        return Response({"updated": len(objects)})

    def _own_restaurant(self) -> Restaurant | None:
        return Restaurant.objects.filter(pk__in=member_restaurant_ids(self.request.user)).first()

    def _invalidate(self) -> None:
        from .cache import bump_menu_version

        for slug in Restaurant.objects.filter(
            pk__in=member_restaurant_ids(self.request.user)
        ).values_list("slug", flat=True):
            bump_menu_version(slug)


class RestaurantViewSet(viewsets.ModelViewSet):
    serializer_class = RestaurantAdminSerializer
    permission_classes = (IsAuthenticated, IsRestaurantMember)

    TRIAL_DAYS = 14

    def get_queryset(self):
        return Restaurant.objects.filter(pk__in=member_restaurant_ids(self.request.user))

    def perform_create(self, serializer):
        """Yangi restoran — 14 kunlik sinov, faqat bir marta va telefon bo'yicha.

        Bepul tarif yo'q, shuning uchun har bir restoran darhol obuna oladi.
        """
        phone = normalize_phone(serializer.validated_data.get("phone", ""))
        if phone and Restaurant.objects.filter(
            phone=phone, trial_used_at__isnull=False
        ).exists():
            raise ValidationError(
                {
                    "phone": "Bu telefon raqami sinov muddatini allaqachon ishlatgan. "
                    "To'lov qiling yoki boshqa raqam kiriting."
                }
            )

        agent = self._agent_from_request()
        # Agent kodi bilan kelganga sinov uzunroq — egasi kodni kiritishdan manfaatdor.
        trial_days = agent_services.AGENT_TRIAL_DAYS if agent else self.TRIAL_DAYS

        now = timezone.now()
        with transaction.atomic():
            # A'zolik `signals.ensure_owner_membership` da beriladi.
            restaurant = serializer.save(owner=self.request.user, trial_used_at=now)
            Subscription.objects.create(
                restaurant=restaurant,
                plan=Plan.objects.get(code="standard"),
                status=Subscription.Status.TRIALING,
                trial_ends_at=now + timedelta(days=trial_days),
            )
            if agent:
                agent_services.attach(restaurant, agent)

    def _agent_from_request(self):
        """Ixtiyoriy `agent_code`. Bo'sh — agentsiz; noto'g'ri — xato (haqqi yo'qolmasin)."""
        code = str(self.request.data.get("agent_code", "") or "").strip()
        if not code:
            return None
        agent = agent_services.find_agent(code)
        if agent is None:
            raise ValidationError({"agent_code": "Bunday agent kodi topilmadi. Tekshirib qayta yozing."})
        return agent

    def destroy(self, request, *args, **kwargs):
        """Restoranni butunlay o'chiradi — faqat egasi, nomini tasdiqlab.

        `confirm` — restoran manzili (slug). Panel ikki bosqichli ogohlantirish
        ko'rsatadi, server esa tasodifiy yoki begona so'rovdan himoya qiladi.
        """
        restaurant = self.get_object()
        if not is_owner(restaurant, request.user):
            raise PermissionDenied("Restoranni faqat egasi o'chira oladi.")
        if str(request.data.get("confirm", "")).strip().lower() != restaurant.slug:
            raise ValidationError(
                {"confirm": "Tasdiqlash uchun restoran manzilini aynan yozing."}
            )
        delete_restaurant(restaurant)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def deletion(self, request, pk=None):
        """O'chirishdan oldingi ogohlantirish uchun: nima yo'qolishi."""
        restaurant = self.get_object()
        if not is_owner(restaurant, request.user):
            raise PermissionDenied("Restoranni faqat egasi o'chira oladi.")
        return Response(deletion_summary(restaurant))

    #: QR ko'rinishi — xodimga "QR kod" ruxsati bilan ochiq; boshqa sozlamalar faqat egasida.
    QR_FIELDS = {"qr_color", "qr_center", "qr_style", "qr_eyes", "qr_frame", "qr_title", "qr_text", "qr_show_link"}

    def perform_update(self, serializer):
        if set(serializer.validated_data) <= self.QR_FIELDS:
            require(serializer.instance, self.request.user, "qr")
        else:
            require_owner(serializer.instance, self.request.user, "Restoran sozlamalarini faqat egasi o'zgartira oladi.")
        before = set(serializer.instance.languages or [])
        # Ro'yxatdan o'tishda orqaga qaytib kodni keyin yozgan bo'lsa ham —
        # faqat hali agent yo'q va sinovda bo'lsa (to'lagandan keyin — admin orqali).
        agent = self._agent_from_request()
        restaurant = serializer.save()
        subscription = getattr(restaurant, "subscription", None)
        if agent and not restaurant.agent_id and subscription and subscription.status == Subscription.Status.TRIALING:
            agent_services.attach(restaurant, agent)
        # Yangi til qo'shilgan bo'lsa, menyudagi hamma matnni tarjima qilamiz.
        if set(restaurant.languages or []) - before:
            transaction.on_commit(lambda: retranslate_restaurant.delay(restaurant.pk))

    @action(detail=False, methods=["get"])
    def languages(self, request):
        """Tanlash mumkin bo'lgan tillar ro'yxati — admin sahifasi uchun."""
        return Response(
            [{"code": code, "name": name} for code, name in LANGUAGE_NAMES.items()]
        )

    @action(detail=False, methods=["get"], url_path="slug-check")
    def slug_check(self, request):
        """Onboarding'da yozayotganda chaqiriladi — butun jadval bo'yicha tekshiradi."""
        slug = normalize_slug(request.query_params.get("slug", ""))
        available = bool(slug) and slug not in RESERVED_SLUGS and not Restaurant.objects.filter(
            slug=slug
        ).exists()
        return Response({"slug": slug, "available": available})

    @action(detail=True, methods=["get"])
    def qr(self, request, pk=None):
        """Restoranning yagona QR kodi — oddiy yoki chop etiladigan kartochka.

        * `format=png` — faqat QR kod (dizayner o'z maketiga qo'yishi uchun).
        * `format=pdf&template=tent|card|sticker|poster` — chop etishga tayyor.
        * `format=image&template=...` — o'sha kartochka PNG sifatida
          (`dpi=60..300`; admin paneldagi ko'rinish kichik DPI bilan so'raydi).
        * `a6` / `a4` — eski havolalar uchun: `card` / `poster` PDF.

        Saqlamasdan ko'rish uchun: `color`, `style`, `eyes`, `center`, `frame`,
        `title`, `text`, `link=0|1`, `inline=1`. `X-QR-Center` va `X-QR-Style`
        sarlavhalari haqiqatan nima chizilganini bildiradi — tanlangan dizayn
        o'qilmasa, soddarog'i beriladi.
        """
        restaurant = self.get_object()
        require(restaurant, request.user, "qr")
        params = request.query_params
        fmt = params.get("format", "png")
        template = params.get("template") or None
        if fmt in ("a6", "a4"):
            fmt, template = "pdf", ("card" if fmt == "a6" else "poster")
        if fmt not in ("png", "pdf", "image"):
            raise ValidationError({"format": "format `png`, `pdf` yoki `image` bo'lishi kerak."})
        if fmt != "png" and template not in qr_print.TEMPLATES:
            raise ValidationError({"template": "Noma'lum shablon."})

        qr_options = _qr_options(params)
        design_options = _design_options(params)
        inline = params.get("inline") in ("1", "true")

        if fmt == "png":
            rendered = qr_codes.restaurant_qr(restaurant, **qr_options)
            buffer = io.BytesIO()
            rendered.image.save(buffer, format="PNG")
            response = HttpResponse(buffer.getvalue(), content_type="image/png")
            filename = f"{restaurant.slug}-qr.png"
        else:
            # Chizilgan QR haqida ma'lumot sarlavhaga ham kerak — bir marta hisoblanadi.
            rendered = qr_codes.restaurant_qr(restaurant, **qr_options)
            options = {
                "qr_options": {**qr_options, "center": rendered.center, "style": rendered.style, "eyes": rendered.eyes},
                "design_options": design_options,
            }
            if fmt == "pdf":
                content = qr_print.render_pdf(restaurant, template, **options)
                response = HttpResponse(content, content_type="application/pdf")
                filename = f"{restaurant.slug}-qr-{template}.pdf"
            else:
                try:
                    dpi = min(max(int(params.get("dpi", qr_print.PRINT_DPI)), 60), qr_print.PRINT_DPI)
                except ValueError as error:
                    raise ValidationError({"dpi": "Son bo'lishi kerak."}) from error
                image = qr_print.render_template(restaurant, template, dpi=dpi, **options)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=dpi < qr_print.PRINT_DPI)
                response = HttpResponse(buffer.getvalue(), content_type="image/png")
                filename = f"{restaurant.slug}-qr-{template}.png"

        disposition = "inline" if inline else "attachment"
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response["X-QR-Center"] = rendered.center
        response["X-QR-Style"] = f"{rendered.style}:{rendered.eyes}"
        return response


def _qr_options(params) -> dict:
    """QR ko'rinishi — oldindan ko'rish uchun so'rovdan (bo'sh bo'lsa saqlangani)."""
    options = {}
    color = params.get("color")
    if color:
        try:
            options["color"] = qr_codes.normalize_color(color)
        except qr_codes.QrColorError as error:
            raise ValidationError({"color": str(error)}) from error
    for key, allowed, message in (
        ("style", qr_codes.STYLES, "Noma'lum QR turi."),
        ("eyes", qr_codes.EYES, "Noma'lum burchak turi."),
        ("center", qr_codes.CENTERS, "Noma'lum QR o'rtasi."),
    ):
        value = params.get(key)
        if value:
            if value not in allowed:
                raise ValidationError({key: message})
            options[key] = value
    return options


def _design_options(params) -> dict:
    """Kartochka yozuvlari va foni — oldindan ko'rish uchun so'rovdan."""
    options = {}
    frame = params.get("frame")
    if frame:
        if frame not in qr_print.FRAMES:
            raise ValidationError({"frame": "Noma'lum fon."})
        options["frame"] = frame
    # Bo'sh qiymat ham ma'noli: "standart matnga qaytish" — shuning uchun `in`.
    if "title" in params:
        options["title"] = params["title"][:24]
    if "text" in params:
        options["text"] = params["text"][:60]
    if "link" in params:
        options["link"] = params["link"] in ("1", "true")
    return options


class CategoryViewSet(OwnerScopedViewSet):
    serializer_class = CategoryAdminSerializer

    def get_queryset(self):
        return Category.objects.filter(
            restaurant_id__in=member_restaurant_ids(self.request.user)
        ).select_related("restaurant")

    def perform_create(self, serializer):
        require(serializer.validated_data["restaurant"], self.request.user, "categories")
        category = serializer.save()
        activity.log(category.restaurant, self.request.user, activity.A.CATEGORY_CREATED, activity.category_title(category))

    def perform_update(self, serializer):
        require(serializer.instance.restaurant, self.request.user, "categories")
        category = serializer.save()
        activity.log(category.restaurant, self.request.user, activity.A.CATEGORY_UPDATED, activity.category_title(category))

    def perform_destroy(self, instance: Category):
        """Ichida taom bo'lsa o'chirishga ruxsat bermaymiz.

        Aks holda kaskad bilan taomlar ham o'chib ketardi — egasi buni
        kutmaydi. Avval taomlarni boshqa kategoriyaga o'tkazish kerak.
        """
        count = instance.dishes.count()
        if count:
            raise ValidationError(
                {
                    "detail": f"Kategoriyada {count} ta taom bor. "
                    "Avval ularni boshqa kategoriyaga o'tkazing."
                }
            )
        require(instance.restaurant, self.request.user, "categories")
        activity.log(instance.restaurant, self.request.user, activity.A.CATEGORY_DELETED, activity.category_title(instance))
        super().perform_destroy(instance)

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        require(self._own_restaurant(), request.user, "categories")
        return super().reorder(request)


class DishViewSet(OwnerScopedViewSet):
    serializer_class = DishAdminSerializer

    def get_queryset(self):
        # Tahrirlagichda taomlar kategoriya tartibida, ichida esa o'z tartibida.
        queryset = (
            Dish.objects.filter(
                category__restaurant_id__in=member_restaurant_ids(self.request.user)
            )
            .select_related("category", "category__restaurant")
            .order_by("category__position", "category_id", "position", "id")
        )
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category_id=category)
        return queryset

    def perform_create(self, serializer):
        require(serializer.validated_data["category"].restaurant, self.request.user, "dishes")
        dish = serializer.save()
        activity.dish_saved(self.request.user, dish, None)

    def perform_update(self, serializer):
        dish = serializer.instance
        for permission in self._needed(dish, serializer.validated_data):
            require(dish.category.restaurant, self.request.user, permission)
        before = activity.dish_snapshot(dish)
        dish = serializer.save()
        activity.dish_saved(self.request.user, dish, before)

    @staticmethod
    def _needed(dish: Dish, data: dict) -> set[str]:
        """Qaysi ruxsat kerak: narx — "prices", mavjudlik — "stoplist", qolgani — "dishes".

        Faqat haqiqatan o'zgargan maydonlar hisobga olinadi — admin panel formani
        butunicha yuborsa ham, faqat narxni o'zgartirgan xodimga "dishes" kerak bo'lmaydi.
        """
        needed = set()
        for key, value in data.items():
            if key == "translation_meta":
                continue
            if key in ("photo", "photo_original"):
                needed.add("dishes")
                continue
            current = dish.category if key == "category" else getattr(dish, key, None)
            if current == value:
                continue
            needed.add({"price": "prices", "is_available": "stoplist"}.get(key, "dishes"))
        return needed

    def perform_destroy(self, instance: Dish):
        restaurant = instance.category.restaurant
        require_owner(restaurant, self.request.user, "Taomni faqat egasi o'chira oladi. Yashirib qo'yishingiz mumkin.")
        activity.log(restaurant, self.request.user, activity.A.DISH_DELETED, activity.dish_title(instance))
        super().perform_destroy(instance)

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        require(self._own_restaurant(), request.user, "dishes")
        return super().reorder(request)


class DishPhotoViewSet(OwnerScopedViewSet):
    """Taom rasmlari — bir taomda bir nechta bo'lishi mumkin."""

    serializer_class = DishPhotoSerializer

    def get_queryset(self):
        queryset = DishPhoto.objects.filter(
            dish__category__restaurant_id__in=member_restaurant_ids(self.request.user)
        ).select_related("dish", "dish__category", "dish__category__restaurant")
        dish = self.request.query_params.get("dish")
        return queryset.filter(dish_id=dish) if dish else queryset

    def perform_create(self, serializer):
        dish = serializer.validated_data["dish"]
        require(dish.category.restaurant, self.request.user, "dishes")
        last = DishPhoto.objects.filter(dish=dish).count()
        serializer.save(position=serializer.validated_data.get("position", last))
        self._invalidate()

    def perform_destroy(self, instance):
        require(instance.dish.category.restaurant, self.request.user, "dishes")
        super().perform_destroy(instance)
        self._invalidate()

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        require(self._own_restaurant(), request.user, "dishes")
        return super().reorder(request)


class TranslatePreviewView(APIView):
    """`POST /api/translate/preview/` — matnni saqlamasdan tarjima qiladi.

    Admin formasidagi "Qayta tarjima qilish" tugmasi shuni chaqiradi.
    Ro'yxat qiymatlar (tarkib) satrga yig'ilib yuboriladi va qaytishda
    yana ro'yxatga bo'linadi.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = TranslatePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        restaurant = Restaurant.objects.filter(
            pk=data["restaurant"], pk__in=member_restaurant_ids(request.user)
        ).first()
        if restaurant is None:
            raise PermissionDenied("Restoran topilmadi.")

        allowed = set(restaurant.languages or [])
        targets = [
            code
            for code in data["targets"]
            if code in allowed and code != restaurant.primary_language
        ]
        if not targets:
            return Response({})

        lists = {key for key, value in data["texts"].items() if isinstance(value, list)}
        flat = {
            key: LIST_SEPARATOR.join(value) if isinstance(value, list) else value
            for key, value in data["texts"].items()
        }

        try:
            translated = get_translator().translate(
                flat, source=restaurant.primary_language, targets=targets
            )
        except TranslationError as error:
            return Response(
                {"detail": f"Tarjima bajarilmadi: {error}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                lang: {
                    key: (
                        [line.strip() for line in text.split(LIST_SEPARATOR) if line.strip()]
                        if key in lists
                        else text
                    )
                    for key, text in values.items()
                }
                for lang, values in translated.items()
            }
        )


class MeView(APIView):
    """Kirgan foydalanuvchi, uning restoranlari va aloqa ma'lumotlari.

    `PATCH` — hisob sozlamalari (ism, aloqa telefoni, Telegram). Bu ma'lumot
    mijozga ko'rinmaydi; platforma egasi bog'lanishi uchun.
    """

    permission_classes = (IsAuthenticated,)

    @staticmethod
    def _profile(user) -> Profile:
        profile, _ = Profile.objects.get_or_create(user=user)
        return profile

    def patch(self, request):
        serializer = ProfileSerializer(
            self._profile(request.user), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def get(self, request):
        restaurants = Restaurant.objects.filter(pk__in=member_restaurant_ids(request.user))
        #: Rol sahifalarni yashirish uchun kerak — to'lov faqat egasida.
        roles = dict(
            RestaurantMember.objects.filter(user=request.user).values_list(
                "restaurant_id", "role"
            )
        )
        return Response(
            {
                "id": request.user.id,
                "phone": request.user.username,
                "name": (
                    self._profile(request.user).full_name
                    or request.user.get_full_name()
                    or request.user.username
                ),
                "profile": ProfileSerializer(self._profile(request.user)).data,
                "role": next(iter(roles.values()), None),
                # "Platforma" bo'limi faqat stolda.uz egasiga ko'rinadi.
                "is_platform_owner": request.user.is_superuser,
                "roles": {str(key): value for key, value in roles.items()},
                # Xodim ruxsatlari — ilova va admin panel ruxsatsiz bo'limni ko'rsatmaydi.
                "permissions": {
                    str(member.restaurant_id): permissions_of(member)
                    for member in RestaurantMember.objects.filter(user=request.user)
                },
                "telegram": bool(self._profile(request.user).telegram_id),
                "restaurants": RestaurantAdminSerializer(
                    restaurants, many=True, context={"request": request}
                ).data,
            }
        )


class IsPlatformOwner(BasePermission):
    """stolda.uz egasi (superuser) — restoran egalari va menejerlar emas."""

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class PlatformOverviewView(APIView):
    """`GET /api/platform/overview/?month=2026-09` — pul, restoranlar, agentlar."""

    permission_classes = (IsAuthenticated, IsPlatformOwner)

    def get(self, request):
        raw = request.query_params.get("month", "")
        today = timezone.localdate()
        try:
            year, month = (int(part) for part in raw.split("-")) if raw else (today.year, today.month)
            if not 1 <= month <= 12 or not 2020 <= year <= 2100:
                raise ValueError
        except ValueError as error:
            raise ValidationError({"month": "Oy YYYY-MM ko'rinishida bo'lsin, masalan 2026-09."}) from error
        return Response(platform.overview(year, month))


class StatsView(APIView):
    """`GET /api/stats/?range=day|week|month&restaurant=<id>` — faqat ko'rishlar."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        restaurant = self._restaurant(request)
        require(restaurant, request.user, "stats")
        if not restaurant.limits["stats"]:
            raise PermissionDenied(
                "Statistika Standard va Pro tariflarida mavjud."
            )

        chosen = request.query_params.get("range", "week")
        days = RANGES.get(chosen, 7)
        now = timezone.localtime()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = today - timedelta(days=days - 1)
        yesterday = today - timedelta(days=1)
        previous_since = since - timedelta(days=days)

        views = MenuView.objects.filter(restaurant=restaurant)
        today_counts = self._counts(views, today, None)
        yesterday_counts = self._counts(views, yesterday, today)
        range_counts = self._counts(views, since, None)
        previous_counts = self._counts(views, previous_since, since)

        dishes = Dish.objects.filter(category__restaurant=restaurant)
        return Response(
            {
                "range": chosen,
                "today": {
                    "dish_opens": today_counts["opens"],
                    "scans": today_counts["scans"],
                },
                "period": {
                    "dish_opens": range_counts["opens"],
                    "scans": range_counts["scans"],
                },
                "trend": {
                    "today_dish_opens": _change(
                        today_counts["opens"], yesterday_counts["opens"]
                    ),
                    "today_scans": _change(
                        today_counts["scans"], yesterday_counts["scans"]
                    ),
                    "period_dish_opens": _change(
                        range_counts["opens"], previous_counts["opens"]
                    ),
                },
                "dishes": {
                    "total": dishes.count(),
                    "hidden": dishes.filter(is_available=False).count(),
                },
                "top_dishes": self._top_dishes(restaurant, since, request),
                "daily": self._daily(views, since, days),
            }
        )

    def _counts(self, views, start, end) -> dict:
        window = views.filter(created_at__gte=start)
        if end is not None:
            window = window.filter(created_at__lt=end)
        return window.aggregate(
            opens=Count("id", filter=Q(kind=ViewKind.DISH_OPEN)),
            scans=Count("id", filter=Q(kind=ViewKind.SCAN)),
        )

    def _restaurant(self, request) -> Restaurant:
        queryset = Restaurant.objects.filter(pk__in=member_restaurant_ids(request.user))
        restaurant_id = request.query_params.get("restaurant")
        if restaurant_id:
            queryset = queryset.filter(pk=restaurant_id)
        restaurant = queryset.first()
        if restaurant is None:
            raise PermissionDenied("Restoran topilmadi.")
        return restaurant

    def _top_dishes(self, restaurant: Restaurant, since, request) -> list[dict]:
        # `views` — MenuView'ning related_name'i, shuning uchun annotatsiya boshqa nomda.
        rows = (
            Dish.objects.filter(category__restaurant=restaurant)
            .select_related("category")
            .annotate(
                view_count=Count(
                    "views",
                    filter=Q(
                        views__kind=ViewKind.DISH_OPEN, views__created_at__gte=since
                    ),
                )
            )
            .order_by("-view_count", "position")[:5]
        )
        return [
            {
                "id": dish.id,
                "name": translate(dish.name),
                "icon": dish.category.icon,
                "photo": (
                    absolute_media_url(dish.photo, request)
                ),
                "views": dish.view_count,
            }
            for dish in rows
        ]

    def _daily(self, views, since, days: int) -> list[dict]:
        """Kunlik ustunlar — taom ochilishi va QR skanerlari alohida.

        Ilgari ikkalasi bitta songa qo'shilardi va grafik kartadagi raqamdan
        katta chiqib, statistika noto'g'ri ishlayotgandek ko'rinardi.
        """
        rows = (
            views.filter(created_at__gte=since)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                opens=Count("id", filter=Q(kind=ViewKind.DISH_OPEN)),
                scans=Count("id", filter=Q(kind=ViewKind.SCAN)),
            )
        )
        counts = {row["day"]: row for row in rows}
        result = []
        for offset in range(days):
            day = (since + timedelta(days=offset)).date()
            row = counts.get(day)
            opens = row["opens"] if row else 0
            scans = row["scans"] if row else 0
            result.append(
                {
                    "date": day.isoformat(),
                    "dish_opens": opens,
                    "scans": scans,
                    #: Moslik uchun — ikkalasining yig'indisi.
                    "views": opens + scans,
                }
            )
        return result


def _change(current: int, previous: int) -> float | None:
    """O'tgan davrga nisbatan o'zgarish, foizda. Taqqoslash imkoni bo'lmasa `None`."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)
