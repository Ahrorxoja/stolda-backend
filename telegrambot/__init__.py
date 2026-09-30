"""To'lov cheklarini Telegram orqali tasdiqlash.

`get_bot()` sozlamalarga qarab tanlaydi. Token bo'lmasa `FakeBot` qaytadi —
shunda dev muhitida va testlarda tashqi so'rov bo'lmaydi (`translation/`
paketi bilan bir xil naqsh).
"""

from .api import TelegramBot, TelegramChatBot
from .base import Bot, ChatBot, TelegramError
from .factory import get_agent_bot, get_bot, get_restaurant_bot
from .fake import FakeBot, FakeChatBot

__all__ = [
    "Bot",
    "ChatBot",
    "FakeBot",
    "FakeChatBot",
    "TelegramBot",
    "TelegramChatBot",
    "TelegramError",
    "get_agent_bot",
    "get_bot",
    "get_restaurant_bot",
]
