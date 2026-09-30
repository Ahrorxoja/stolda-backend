"""Keyingi to'lovlardan 20% — muddatsiz (agent faol ekan).

Ilgari sukut 12 oy edi. Siyosat hammaga bir xil o'zgargani uchun mavjud
agentlarning 12 oylik cheklovi ham olib tashlanadi; alohida muddat kerak
bo'lsa Django admin'da qayta yoziladi.
"""

from django.db import migrations, models


def unlimited(apps, schema_editor):
    apps.get_model("agents", "Agent").objects.filter(months=12).update(months=0)


def back(apps, schema_editor):
    apps.get_model("agents", "Agent").objects.filter(months=0).update(months=12)


class Migration(migrations.Migration):
    dependencies = [("agents", "0003_first_payment_percent")]

    operations = [
        migrations.AlterField(
            model_name="agent",
            name="months",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="0 — cheksiz (agent faol ekan). Aks holda birinchi to'lovdan boshlab shuncha oy.",
                verbose_name="Necha oy davomida",
            ),
        ),
        migrations.RunPython(unlimited, back),
    ]
