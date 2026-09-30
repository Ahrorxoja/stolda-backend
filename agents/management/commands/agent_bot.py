"""Agentlar botini tinglaydi (long polling).

Ishga tushirish: `python manage.py agent_bot` — `AGENT_BOT_TOKEN` kerak.
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from agents.bot import MENU_COMMANDS, handle_message
from telegrambot import TelegramError, get_agent_bot

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 25
ERROR_PAUSE = 5


class Command(BaseCommand):
    help = "Agentlar botini ishga tushiradi: havola, balans, pul yechish."

    def handle(self, *args, **options):
        if not settings.AGENT_BOT_TOKEN:
            raise CommandError("AGENT_BOT_TOKEN sozlanmagan (.env).")

        bot = get_agent_bot()
        offset = 0
        try:
            bot.set_commands(MENU_COMMANDS)
        except TelegramError as error:
            logger.warning("Buyruqlar ro'yxati o'rnatilmadi: %s", error)
        self.stdout.write("Agentlar boti ishga tushdi…")

        while True:
            try:
                updates = bot.get_updates(offset=offset, timeout=POLL_TIMEOUT)
            except TelegramError as error:
                logger.warning("getUpdates: %s", error)
                time.sleep(ERROR_PAUSE)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                try:
                    handle_message(bot, message)
                except TelegramError as error:
                    logger.warning("Agentga javob yuborilmadi: %s", error)
                except Exception:  # noqa: BLE001 — bitta xabar botni to'xtatmasin
                    logger.exception("Agent xabarini qayta ishlashda xato")
