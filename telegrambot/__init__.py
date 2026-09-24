"""To'lov cheklarini Telegram orqali tasdiqlash.

`get_bot()` sozlamalarga qarab tanlaydi. Token bo'lmasa `FakeBot` qaytadi —
shunda dev muhitida va testlarda tashqi so'rov bo'lmaydi (`translation/`
paketi bilan bir xil naqsh).
"""

from .api import TelegramBot
from .base import Bot, TelegramError
from .factory import get_bot
from .fake import FakeBot

__all__ = ["Bot", "FakeBot", "TelegramBot", "TelegramError", "get_bot"]
