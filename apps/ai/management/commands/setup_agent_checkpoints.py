from django.core.management.base import BaseCommand, CommandError

from apps.ai.infrastructure.checkpoints import postgres_saver


class Command(BaseCommand):
    help = "Create or migrate the official LangGraph PostgresSaver tables."

    def handle(self, *args, **options):
        try:
            with postgres_saver() as saver:
                saver.setup()
        except ImportError as exc:
            raise CommandError(
                "Install the pinned LangGraph dependencies before setup."
            ) from exc
        self.stdout.write(self.style.SUCCESS("LangGraph checkpoints are ready."))
