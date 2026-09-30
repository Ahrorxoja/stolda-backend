"""Telegram Mini App `initData` imzosini tekshirish — agentlar va restoranlar ilovasi uchun.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

#: initData shuncha vaqtdan eski bo'lsa — qayta ochish kerak (Telegram har ochilishda yangisini beradi).
INIT_DATA_MAX_AGE = 24 * 60 * 60


def verify_init_data(init_data: str, token: str, *, now: float | None = None) -> dict | None:
    """Telegram imzosini tekshiradi. To'g'ri bo'lsa — `user` (dict), aks holda `None`.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not token:
        return None
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user = json.loads(fields.get("user", "{}"))
    except (TypeError, ValueError):
        return None
    if (now or time.time()) - auth_date > INIT_DATA_MAX_AGE or not user.get("id"):
        return None
    return user
