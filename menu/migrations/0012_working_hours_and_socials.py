"""Erkin matnli ish vaqti o'rniga haftalik jadval, va ijtimoiy sahifalar.

Eski `hours` matnidan vaqt ajratib olinadi (`"Har kuni 10:00 – 23:00"` →
har kunga 10:00–23:00). Vaqt topilmasa sukut bo'yicha jadval qoladi.
"""

import menu.hours
from django.db import migrations, models


def fill_working_hours(apps, schema_editor):
    Restaurant = apps.get_model("menu", "Restaurant")
    for restaurant in Restaurant.objects.all():
        text = " ".join(
            value for value in (restaurant.hours or {}).values() if isinstance(value, str)
        )
        parsed = menu.hours.parse_hours_text(text)
        if parsed:
            restaurant.working_hours = parsed
            restaurant.save(update_fields=["working_hours"])


class Migration(migrations.Migration):

    dependencies = [
        ("menu", "0011_category_photo_original_dishphoto_original"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="working_hours",
            field=models.JSONField(
                blank=True,
                default=menu.hours.default_working_hours,
                validators=[menu.hours.validate_working_hours],
            ),
        ),
        migrations.RunPython(fill_working_hours, migrations.RunPython.noop),
        migrations.RemoveField(model_name="restaurant", name="hours"),
        migrations.AddField(
            model_name="restaurant",
            name="facebook",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="telegram",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AlterField(
            model_name="restaurant",
            name="instagram",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
