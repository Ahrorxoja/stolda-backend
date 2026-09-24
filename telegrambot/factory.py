from django.conf import settings

from .api import TelegramBot
from .base import Bot
from .fake import FakeBot


def get_bot() -> Bot:
    """Sozlamalarga qarab botni qaytaradi.

    `TELEGRAM_BOT_TOKEN` yoki `TELEGRAM_ADMIN_CHAT_ID` bo'lmasa `FakeBot` —
    shunda dev muhitida va testlarda tashqi so'rov yuborilmaydi, chek
    yuklash oqimi esa baribir to'liq ishlaydi (`get_translator()` kabi).
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")
    if not token or not chat_id:
        return FakeBot()
    return TelegramBot(token, chat_id)
