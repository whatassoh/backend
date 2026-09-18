import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.application.services import (
    KnowledgeIngestionService,
    KnowledgeSourceInput,
)


class Command(BaseCommand):
    help = "Ingest validated KnowledgeSource definitions from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", required=True, help="Path to a JSON source or list."
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Unable to read knowledge input: {exc}") from exc
        entries = payload if isinstance(payload, list) else [payload]
        service = KnowledgeIngestionService()
        for entry in entries:
            source = service.ingest(KnowledgeSourceInput(**entry))
            self.stdout.write(self.style.SUCCESS(f"Ingested {source}"))
