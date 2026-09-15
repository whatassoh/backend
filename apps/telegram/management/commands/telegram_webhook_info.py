from django.core.management.base import BaseCommand

from apps.telegram.providers.bot_api import TelegramProvider


class Command(BaseCommand):
    help = "Show Telegram Bot API webhook information."

    def handle(self, *args, **options):
        result = TelegramProvider().get_webhook_info()
        self.stdout.write(str(result))
