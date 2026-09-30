"""Telegram botni tinglaydi: chek tasdiqlandimi yoki rad etildimi.

Uzun so'rov (long polling) — webhook uchun ochiq HTTPS manzil kerak bo'lardi,
polling esa dev va production'da bir xil ishlaydi.

Ishga tushirish: `python manage.py telegram_bot`
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from agents.models import AgentWithdrawal
from agents.services import settle_withdrawal
from menu.billing_ledger import approve_receipt, reject_receipt
from menu.models import PaymentReceipt
from telegrambot import TelegramError, get_bot

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 25
ERROR_PAUSE = 5


class Command(BaseCommand):
    help = "Telegram'dagi chek tasdiqlash va agent pul yechish tugmalarini tinglaydi."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_ADMIN_CHAT_ID:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN va TELEGRAM_ADMIN_CHAT_ID sozlanmagan (.env)."
            )

        bot = get_bot()
        offset = 0
        self.stdout.write("Telegram bot ishga tushdi, cheklar kutilmoqda…")

        while True:
            try:
                updates = bot.get_updates(offset=offset, timeout=POLL_TIMEOUT)
            except TelegramError as error:
                logger.warning("getUpdates: %s", error)
                time.sleep(ERROR_PAUSE)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                callback = update.get("callback_query")
                if callback:
                    self._handle_callback(bot, callback)

    def _handle_callback(self, bot, callback: dict) -> None:
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        # Faqat platforma egasining chati tasdiqlay oladi.
        if chat_id != str(settings.TELEGRAM_ADMIN_CHAT_ID):
            logger.warning("Begona chatdan callback: %s", chat_id)
            return

        action, _, raw_id = str(callback.get("data", "")).partition(":")
        if action in ("wd_paid", "wd_reject"):
            self._handle_withdrawal(bot, callback, action, raw_id)
            return

        receipt = PaymentReceipt.objects.filter(pk=raw_id or 0).first()
        if receipt is None:
            self._answer(bot, callback, "Chek topilmadi.")
            return

        if action == "approve":
            invoice = approve_receipt(receipt)
            text = "✅ Tasdiqlandi" if invoice else "Bu chek allaqachon ko'rib chiqilgan."
        elif action == "reject":
            done = reject_receipt(receipt, note="Chek tasdiqlanmadi.")
            text = "❌ Rad etildi" if done else "Bu chek allaqachon ko'rib chiqilgan."
        else:
            text = "Noma'lum buyruq."

        self._answer(bot, callback, text)

        message_id = str(callback.get("message", {}).get("message_id", ""))
        if message_id:
            caption = str(callback.get("message", {}).get("caption", ""))
            try:
                bot.edit_caption(message_id, f"{caption}\n\n<b>{text}</b>")
            except TelegramError as error:
                logger.warning("Xabarni yangilab bo'lmadi: %s", error)

    def _handle_withdrawal(self, bot, callback: dict, action: str, raw_id: str) -> None:
        """Agentning pul yechish so'rovi: "O'tkazdim" yoki "Rad etish"."""
        withdrawal = AgentWithdrawal.objects.filter(pk=raw_id if raw_id.isdigit() else 0).first()
        if withdrawal is None:
            self._answer(bot, callback, "So'rov topilmadi.")
            return

        paid = action == "wd_paid"
        done = settle_withdrawal(withdrawal, paid=paid)
        if not done:
            text = "Bu so'rov allaqachon ko'rib chiqilgan."
        else:
            text = "✅ O'tkazildi" if paid else "❌ Rad etildi"
        self._answer(bot, callback, text)

        message = callback.get("message", {})
        message_id = str(message.get("message_id", ""))
        if message_id and done:
            try:
                bot.edit_text(message_id, f"{message.get('text', '')}\n\n<b>{text}</b>")
            except TelegramError as error:
                logger.warning("Xabarni yangilab bo'lmadi: %s", error)

    @staticmethod
    def _answer(bot, callback: dict, text: str) -> None:
        try:
            bot.answer_callback(str(callback.get("id", "")), text)
        except TelegramError as error:
            logger.warning("answerCallbackQuery: %s", error)
