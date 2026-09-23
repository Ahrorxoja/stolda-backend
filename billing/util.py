"""Provayderlar orasida umumiy — webhook'dan obunani topish."""


def get_subscription(restaurant_id):
    """`menu` ilovasiga bog'liqlik faqat shu yerda, chaqiruv vaqtida — aylanma import bo'lmasin."""
    from menu.models import Subscription

    if not restaurant_id:
        return None
    return (
        Subscription.objects.filter(restaurant_id=restaurant_id)
        .select_related("restaurant", "plan")
        .first()
    )
