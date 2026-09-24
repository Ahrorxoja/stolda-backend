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

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        return []
