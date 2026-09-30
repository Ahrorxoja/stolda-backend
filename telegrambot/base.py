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

    def send_buttons(self, text: str, buttons: list[tuple[str, str]]) -> str:
        """Matnli xabar + bir qator tugma (`(yozuv, callback_data)`), `message_id`."""
        ...

    def edit_text(self, message_id: str, text: str) -> None:
        """Matnli xabarni yangilaydi va tugmalarni olib tashlaydi."""
        ...

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        """Uzun so'rov (long polling) — kelgan yangilanishlar."""
        ...


class ChatBot(Protocol):
    """Ko'p foydalanuvchili bot (agentlar) — har xabar kimga ekani aniq beriladi."""

    def send(self, chat_id: str, text: str, keyboard: list[list] | None = None) -> str:
        """Xabar; `keyboard` — pastdagi tugmalar (qatorlar bo'yicha), `[]` — olib tashlash.

        Tugma — matn yoki `{"text": ..., "request_contact": True}`.
        """
        ...

    def send_photo(self, chat_id: str, image: bytes, caption: str = "") -> str:
        """PNG rasm (masalan agentning QR vizitkasi)."""
        ...

    def send_document(self, chat_id: str, data: bytes, filename: str, caption: str = "") -> str:
        """Fayl (masalan PDF qo'llanma)."""
        ...

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Telegram'dagi "Menu" ro'yxati: `[("start", "Boshlash"), ...]`."""
        ...

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        """Kelgan xabarlar (long polling)."""
        ...
