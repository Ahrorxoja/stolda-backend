"""Telegram Bot API — chekni yuborish va tasdiqlash tugmalarini kuzatish."""

import json

import requests

from .base import TelegramError

API_ROOT = "https://api.telegram.org"


class TelegramBot:
    """Haqiqiy bot.

    **Token hech qachon xato matniga tushmaydi.** U API manzilining ichida
    turadi (`/bot<TOKEN>/sendPhoto`), shuning uchun `requests` xatolari uni
    ochib qo'yadi — har bir xato `from None` bilan uziladi va matn o'zimiz
    yozgan qatordan iborat bo'ladi.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout: int = 30,
        session: requests.Session | None = None,
    ):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN berilmagan.")
        self._token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.session = session or requests.Session()

    def _call(self, method: str, *, data=None, files=None, timeout: int | None = None):
        try:
            response = self.session.post(
                f"{API_ROOT}/bot{self._token}/{method}",
                data=data,
                files=files,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException:
            # Asl xatoda manzil (ya'ni token) bor — zanjirni uzamiz.
            raise TelegramError(f"Telegram'ga ulanib bo'lmadi ({method}).") from None

        if response.status_code == 401:
            raise TelegramError("Bot tokeni qabul qilinmadi (401). Tokenni tekshiring.")
        if response.status_code == 429:
            raise TelegramError("Telegram limiti (429). Birozdan keyin urinib ko'ring.")

        try:
            payload = response.json()
        except ValueError:
            raise TelegramError(
                f"Telegram tushunarsiz javob qaytardi ({method}, {response.status_code})."
            ) from None

        if not payload.get("ok"):
            description = str(payload.get("description", ""))[:200]
            raise TelegramError(f"Telegram rad etdi ({method}): {description}")
        return payload.get("result")

    def send_receipt(self, *, caption: str, image, approve: str, reject: str) -> str:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash", "callback_data": approve},
                    {"text": "❌ Rad etish", "callback_data": reject},
                ]
            ]
        }
        result = self._call(
            "sendPhoto",
            data={
                "chat_id": self.chat_id,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(keyboard),
            },
            files={"photo": ("chek.webp", image, "image/webp")},
        )
        return str(result.get("message_id", ""))

    def send_message(self, text: str) -> str:
        result = self._call(
            "sendMessage",
            data={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        return str(result.get("message_id", ""))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", data={"callback_query_id": callback_id, "text": text})

    def edit_caption(self, message_id: str, caption: str) -> None:
        self._call(
            "editMessageCaption",
            data={
                "chat_id": self.chat_id,
                "message_id": message_id,
                "caption": caption,
                "parse_mode": "HTML",
            },
        )

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        result = self._call(
            "getUpdates",
            data={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": json.dumps(["callback_query"]),
            },
            # HTTP kutish uzun so'rovdan uzunroq bo'lishi kerak.
            timeout=timeout + 10,
        )
        return result or []
