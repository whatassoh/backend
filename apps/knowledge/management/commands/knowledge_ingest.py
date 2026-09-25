import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.knowledge.application.services import (
    KnowledgeIngestionService,
    KnowledgeSourceInput,
)
from apps.knowledge.domain.exceptions import KnowledgeValidationError


class Command(BaseCommand):
    help = "Ingest validated KnowledgeSource definitions from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", required=True, help="Path to a JSON source or list."
        )
        parser.add_argument(
            "--fail-on-quarantine",
            action="store_true",
            help="Return a failure when any source is quarantined.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["file"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Unable to read knowledge input: {exc}") from exc
        entries = payload if isinstance(payload, list) else [payload]
        if not entries or not all(isinstance(entry, dict) for entry in entries):
            raise CommandError("Knowledge input must contain an object or object list.")
        service = KnowledgeIngestionService()
        quarantined = []
        for entry in entries:
            try:
                source = service.ingest(KnowledgeSourceInput(**entry))
            except (TypeError, ValueError, KnowledgeValidationError) as exc:
                raise CommandError(f"Invalid knowledge entry: {exc}") from exc
            if source.status == "QUARANTINED":
                quarantined.append(str(source))
                self.stdout.write(self.style.WARNING(f"Quarantined {source}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Ingested {source}"))
        if quarantined and options["fail_on_quarantine"]:
            raise CommandError(
                f"{len(quarantined)} knowledge source(s) were quarantined."
            )
