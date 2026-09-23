"""Celery ilovasi — AI tarjima fon vazifalari uchun.

Broker berilmasa (`CELERY_BROKER_URL` bo'sh) vazifalar darhol, shu jarayonda
bajariladi. Shu sababli dev muhitida va testlarda Redis shart emas.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("stolda")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
