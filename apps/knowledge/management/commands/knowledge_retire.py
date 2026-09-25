from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.knowledge.application.cache import bump_corpus_revision
from apps.knowledge.domain.enums import KnowledgeSourceStatus
from apps.knowledge.infrastructure.models import KnowledgeSource


class Command(BaseCommand):
    help = "Deactivate a knowledge source version without deleting its lineage."

    def add_arguments(self, parser):
        parser.add_argument("key")
        parser.add_argument("--namespace", default="customer-help")
        parser.add_argument("--version", type=int)

    @transaction.atomic
    def handle(self, *args, **options):
        sources = KnowledgeSource.objects.select_for_update().filter(
            namespace=options["namespace"],
            key=options["key"],
            status=KnowledgeSourceStatus.ACTIVE,
        )
        if options["version"]:
            sources = sources.filter(version=options["version"])
        updated = sources.update(status=KnowledgeSourceStatus.INACTIVE)
        if not updated:
            raise CommandError("No active source matched the selection.")
        transaction.on_commit(lambda: bump_corpus_revision(options["namespace"]))
        self.stdout.write(self.style.SUCCESS(f"Retired {updated} source(s)."))
