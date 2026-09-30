"""Token sozlanmaganda — tarmoqqa chiqmaydi, chaqiruvlarni yozib boradi."""

import itertools


class FakeBot:
    """Dev muhiti va testlar uchun. Yuborilgan xabarlarni ro'yxatda saqlaydi."""

    def __init__(self):
        self.sent: list[dict] = []
        self.answered: list[dict] = []
        self.edited: list[dict] = []
        self._ids = itertools.count(1)

    def send_receipt(self, *, caption: str, image, approve: str, reject: str) -> str:
        message_id = str(next(self._ids))
        self.sent.append(
            {
                "message_id": message_id,
                "caption": caption,
                "approve": approve,
                "reject": reject,
            }
        )
        return message_id

    def send_message(self, text: str) -> str:
        message_id = str(next(self._ids))
        self.sent.append({"message_id": message_id, "text": text})
        return message_id

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.answered.append({"callback_id": callback_id, "text": text})

    def edit_caption(self, message_id: str, caption: str) -> None:
        self.edited.append({"message_id": message_id, "caption": caption})

    def send_buttons(self, text: str, buttons: list[tuple[str, str]]) -> str:
        message_id = str(next(self._ids))
        self.sent.append({"message_id": message_id, "text": text, "buttons": buttons})
        return message_id

    def edit_text(self, message_id: str, text: str) -> None:
        self.edited.append({"message_id": message_id, "text": text})

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        return []


class FakeChatBot:
    """Agentlar botining soxtasi — kimga nima yuborilganini yozib boradi."""

    def __init__(self):
        self.sent: list[dict] = []
        self._ids = itertools.count(1)

    def send(self, chat_id: str, text: str, keyboard: list[list] | None = None) -> str:
        message_id = str(next(self._ids))
        self.sent.append({"chat_id": str(chat_id), "text": text, "keyboard": keyboard})
        return message_id

    def send_photo(self, chat_id: str, image: bytes, caption: str = "") -> str:
        message_id = str(next(self._ids))
        self.sent.append({"chat_id": str(chat_id), "photo": image, "caption": caption})
        return message_id

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        return []

    def send_document(self, chat_id: str, data: bytes, filename: str, caption: str = "") -> str:
        message_id = str(next(self._ids))
        self.sent.append({"chat_id": str(chat_id), "document": filename, "caption": caption})
        return message_id

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.commands = list(commands)

    def texts(self, chat_id: str) -> list[str]:
        return [item.get("text") or item.get("caption", "") for item in self.sent if item["chat_id"] == str(chat_id)]
