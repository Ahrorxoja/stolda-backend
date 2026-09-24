"""Pro tarifni ommaviy ro'yxatdan olib tashlaydi.

`0006` uni `is_public=True` bilan yaratgan edi, holbuki narxi 0 va sotib
bo'lmaydi ("tez orada"). Shunday holda `GET /api/billing/` uni 0 so'mlik
tarif sifatida qaytarardi. Narx va imkoniyatlar aniqlangach qayta yoqiladi.
"""

from django.db import migrations


def hide_pro(apps, schema_editor):
    apps.get_model("menu", "Plan").objects.filter(code="pro").update(is_public=False)


def show_pro(apps, schema_editor):
    apps.get_model("menu", "Plan").objects.filter(code="pro").update(is_public=True)


class Migration(migrations.Migration):
    dependencies = [("menu", "0009_remove_menuview_table_delete_table")]

    operations = [migrations.RunPython(hide_pro, show_pro)]
