from django.core.management.base import BaseCommand, CommandError

from apps.telegram.exceptions import TelegramAPIError, TelegramConfigurationError
from apps.telegram.providers.bot_api import TelegramProvider


class Command(BaseCommand):
    help = "Reply to pending Telegram text messages once, using long polling."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=20,
            help="Long-polling wait in seconds (0 to 50; default: 20).",
        )
        parser.add_argument(
            "--reply",
            default="Message bien recu !",
            help="Reply sent for each received text message.",
        )

    def handle(self, *args, **options):
        try:
            provider = TelegramProvider()
            updates = provider.get_updates(timeout=options["timeout"])
        except (TelegramConfigurationError, TelegramAPIError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        replied = 0
        for update in updates:
            message = update.get("message")
            if not isinstance(message, dict) or not message.get("text"):
                continue
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue
            provider.send_text(str(chat_id), options["reply"])
            replied += 1

        self.stdout.write(self.style.SUCCESS(f"Telegram: {replied} response(s) sent."))
