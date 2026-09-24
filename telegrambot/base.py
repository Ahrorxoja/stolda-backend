from typing import Protocol


class TelegramError(RuntimeError):
    """Telegram API javob bermadi yoki so'rovni rad etdi.

    Xabar matnida bot tokeni bo'lmasligi kerak — token API manzilining ichida
    turadi va `requests` xatolari uni ochib qo'yadi.
    """


class Bot(Protocol):
    """Telegram bot interfeysi — chekni yuborish va javobini yangilash."""

    def send_receipt(self, *, caption: str, image, approve: str, reject: str) -> str:
        """Chek rasmini tasdiqlash tugmalari bilan yuboradi, `message_id` qaytaradi.

        `approve`/`reject` — tugmalarning `callback_data` qiymatlari.
        """
        ...

    def send_message(self, text: str) -> str:
        """Tugmasiz oddiy xabar — kunlik eslatmalar uchun."""
        ...

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Tugma bosilganda Telegram kutayotgan javob (soat aylanishi to'xtaydi)."""
        ...

    def edit_caption(self, message_id: str, caption: str) -> None:
        """Yechim qabul qilingach xabarni yangilaydi va tugmalarni olib tashlaydi."""
        ...

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        """Uzun so'rov (long polling) — kelgan yangilanishlar."""
        ...
