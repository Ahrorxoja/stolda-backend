from django.conf import settings

from .api import TelegramBot, TelegramChatBot
from .base import Bot, ChatBot
from .fake import FakeBot, FakeChatBot


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


def get_restaurant_bot() -> ChatBot:
    """@Stoldabot — restoran egalari va xodimlariga xabar (cheklar boti bilan bir token)."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    return TelegramChatBot(token) if token else FakeChatBot()


def get_agent_bot() -> ChatBot:
    """Agentlar boti. `AGENT_BOT_TOKEN` bo'lmasa `FakeChatBot` — tarmoqqa chiqmaydi."""
    token = getattr(settings, "AGENT_BOT_TOKEN", "")
    return TelegramChatBot(token) if token else FakeChatBot()
