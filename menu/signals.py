"""Menyu o'zgarganda public kesh versiyasini oshiradi."""

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import translation_sync as sync
from .cache import bump_menu_version
from .models import Category, Dish, Restaurant
from .tasks import retranslate


def _slug_of(instance) -> str | None:
    if isinstance(instance, Restaurant):
        return instance.slug
    if isinstance(instance, Category):
        return instance.restaurant.slug
    if isinstance(instance, Dish):
        return instance.category.restaurant.slug
    return None


@receiver(post_save, sender=Restaurant)
@receiver(post_save, sender=Category)
@receiver(post_save, sender=Dish)
@receiver(post_delete, sender=Restaurant)
@receiver(post_delete, sender=Category)
@receiver(post_delete, sender=Dish)
def invalidate_menu_cache(sender, instance, **kwargs) -> None:
    slug = _slug_of(instance)
    if slug:
        bump_menu_version(slug)


@receiver(post_save, sender=Restaurant)
@receiver(post_save, sender=Category)
@receiver(post_save, sender=Dish)
def schedule_translation(sender, instance, **kwargs) -> None:
    """Asosiy tildagi matn o'zgargan bo'lsa, qolgan tillarni navbatga qo'yadi.

    Task obyektni qayta saqlaydi va shu signal yana ishlaydi — lekin o'shanda
    matn xeshi mos keladi va navbatga hech narsa qo'yilmaydi.
    """
    restaurant = sync.restaurant_of(instance)
    if restaurant is None or not restaurant.auto_translate:
        return
    if not sync.pending_languages(instance, restaurant):
        return

    model_name, pk = type(instance).__name__, instance.pk
    transaction.on_commit(lambda: retranslate.delay(model_name, pk))
