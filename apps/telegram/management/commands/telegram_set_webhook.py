from django.core.management.base import BaseCommand

from apps.telegram.providers.bot_api import TelegramProvider


class Command(BaseCommand):
    help = "Configure the Telegram Bot API webhook."

    def handle(self, *args, **options):
        result = TelegramProvider().set_webhook()
        self.stdout.write(self.style.SUCCESS(f"Telegram webhook configured: {result}"))
