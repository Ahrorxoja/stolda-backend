"""QR o'rtasi uchta holatli bo'ladi va chop etiladigan kartochka sozlamalari.

`qr_logo` (ha/yo'q) o'rniga `qr_center`: `icon` (vilka-pichoq, yangi sukut),
`logo` yoki `none`. Logotipni yoqib qo'ygan restoranlar `logo` da qoladi,
qolganlari vilka-pichoq oladi — oddiy QR'ning "bu nima?" muammosi uchun.
"""

from django.db import migrations, models


def forward(apps, schema_editor):
    Restaurant = apps.get_model("menu", "Restaurant")
    Restaurant.objects.filter(qr_logo=True).update(qr_center="logo")


def backward(apps, schema_editor):
    Restaurant = apps.get_model("menu", "Restaurant")
    Restaurant.objects.filter(qr_center="logo").update(qr_logo=True)


class Migration(migrations.Migration):
    dependencies = [("menu", "0019_restaurant_qr_shape")]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="qr_center",
            field=models.CharField(
                choices=[("icon", "Vilka-pichoq"), ("logo", "Logotip"), ("none", "Bo'sh")],
                default="icon",
                max_length=8,
                verbose_name="QR o'rtasi",
            ),
        ),
        migrations.RunPython(forward, backward),
        migrations.RemoveField(model_name="restaurant", name="qr_logo"),
        migrations.AddField(
            model_name="restaurant",
            name="qr_frame",
            field=models.CharField(
                choices=[("light", "Och"), ("dark", "To'q")],
                default="light",
                max_length=8,
                verbose_name="QR kartochka foni",
            ),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="qr_title",
            field=models.CharField(blank=True, max_length=24, verbose_name="QR sarlavhasi"),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="qr_text",
            field=models.CharField(blank=True, max_length=60, verbose_name="QR izohi"),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="qr_show_link",
            field=models.BooleanField(default=True, verbose_name="Kartochkada havola"),
        ),
    ]
