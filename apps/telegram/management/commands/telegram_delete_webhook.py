from django.core.management.base import BaseCommand

from apps.telegram.providers.bot_api import TelegramProvider


class Command(BaseCommand):
    help = "Delete the Telegram Bot API webhook."

    def add_arguments(self, parser):
        parser.add_argument("--drop-pending-updates", action="store_true")

    def handle(self, *args, **options):
        result = TelegramProvider().delete_webhook(
            drop_pending_updates=options["drop_pending_updates"]
        )
        self.stdout.write(self.style.SUCCESS(f"Telegram webhook deleted: {result}"))
