"""Restoranni butunlay o'chirish.

Faqat egasi o'chira oladi va o'chirish darhol, qaytarib bo'lmaydi: menyu,
rasmlar, obuna, cheklar, hisob-fakturalar, statistika va menejerlarning
a'zoligi — hammasi. Egasining o'z hisobi (Google/telefon bilan kirish)
qoladi — u keyin yangi restoran ochishi mumkin.

Django `CASCADE` qatorlarni o'chiradi, lekin diskdagi fayllarni emas —
shuning uchun rasmlar alohida, tranzaksiya muvaffaqiyatli tugagach o'chiriladi.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .models import (
    Category,
    Dish,
    DishPhoto,
    Invoice,
    MenuView,
    PaymentReceipt,
    Restaurant,
    RestaurantInvite,
    RestaurantMember,
    Subscription,
)

logger = logging.getLogger(__name__)


def deletion_summary(restaurant: Restaurant) -> dict:
    """O'chirishdan oldin egasiga ko'rsatiladigan ro'yxat — nima yo'qoladi."""
    categories = Category.objects.filter(restaurant=restaurant)
    photos = DishPhoto.objects.filter(dish__category__restaurant=restaurant).count()
    photos += categories.exclude(photo="").exclude(photo__isnull=True).count()
    photos += sum(1 for field in (restaurant.logo, restaurant.cover) if field)

    subscription = Subscription.objects.filter(restaurant=restaurant).first()
    paid_until = None
    if subscription is not None:
        if subscription.status == Subscription.Status.TRIALING:
            paid_until = subscription.trial_ends_at
        elif subscription.status == Subscription.Status.ACTIVE:
            paid_until = subscription.current_period_end
    days_left = (
        max((paid_until - timezone.now()).days, 0)
        if paid_until and paid_until > timezone.now()
        else 0
    )

    return {
        "name": restaurant.name,
        "slug": restaurant.slug,
        "categories": categories.count(),
        "dishes": Dish.objects.filter(category__restaurant=restaurant).count(),
        "photos": photos,
        "views": MenuView.objects.filter(restaurant=restaurant).count(),
        "managers": RestaurantMember.objects.filter(restaurant=restaurant)
        .exclude(role=RestaurantMember.Role.OWNER)
        .count(),
        "invites": RestaurantInvite.objects.filter(
            restaurant=restaurant, accepted_at__isnull=True
        ).count(),
        "receipts": PaymentReceipt.objects.filter(
            subscription__restaurant=restaurant
        ).count(),
        "invoices": Invoice.objects.filter(
            subscription__restaurant=restaurant, status=Invoice.Status.PAID
        ).count(),
        "subscription_status": subscription.status if subscription else None,
        "paid_until": paid_until,
        "days_left": days_left,
    }


def _files_of(restaurant: Restaurant) -> list:
    """O'chiriladigan barcha fayllar (`FieldFile`) — qatorlar o'chmasdan oldin."""
    files = [restaurant.logo, restaurant.cover]
    for category in Category.objects.filter(restaurant=restaurant):
        files += [category.photo, category.photo_original]
    for photo in DishPhoto.objects.filter(dish__category__restaurant=restaurant):
        files += [photo.image, photo.original]
    for receipt in PaymentReceipt.objects.filter(subscription__restaurant=restaurant):
        files.append(receipt.image)
    return [(field.storage, field.name) for field in files if field]


def delete_restaurant(restaurant: Restaurant) -> None:
    files = _files_of(restaurant)
    slug = restaurant.slug

    def remove_files() -> None:
        for storage, name in files:
            try:
                storage.delete(name)
            except OSError:
                # Fayl allaqachon yo'q bo'lsa ham o'chirish to'xtamasin.
                logger.warning("Fayl o'chirilmadi: %s", name)

    with transaction.atomic():
        restaurant.delete()
        transaction.on_commit(remove_files)
    logger.info("Restoran o'chirildi: %s", slug)
