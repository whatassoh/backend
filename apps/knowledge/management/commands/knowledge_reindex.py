from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.application.cache import bump_corpus_revision
from apps.knowledge.application.ingestion import KnowledgeIngestionService
from apps.knowledge.domain.enums import (
    KnowledgeEmbeddingStatus,
    KnowledgeSourceStatus,
)
from apps.knowledge.infrastructure.models import KnowledgeSource
from apps.knowledge.tasks import index_source_embeddings


class Command(BaseCommand):
    help = "Rebuild lexical indexes and optionally semantic embeddings."

    def add_arguments(self, parser):
        parser.add_argument("--namespace", default="customer-help")
        parser.add_argument("--key")
        parser.add_argument("--embeddings", action="store_true")
        parser.add_argument("--reset-failed", action="store_true")

    def handle(self, *args, **options):
        sources = KnowledgeSource.objects.filter(
            namespace=options["namespace"], status=KnowledgeSourceStatus.ACTIVE
        ).order_by("key", "-version")
        if options["key"]:
            sources = sources.filter(key=options["key"])
        if not sources.exists():
            raise CommandError("No active knowledge source matched the selection.")
        if options["embeddings"] and not settings.AI_RAG_SEMANTIC_ENABLED:
            raise CommandError("AI_RAG_SEMANTIC_ENABLED must be true.")

        indexed = 0
        for source in sources:
            KnowledgeIngestionService._refresh_search_vectors(source)
            if options["reset_failed"]:
                source.chunks.filter(
                    embedding_status=KnowledgeEmbeddingStatus.FAILED
                ).update(
                    embedding_status=KnowledgeEmbeddingStatus.PENDING,
                    embedding_attempts=0,
                    embedding_error="",
                )
            if options["embeddings"]:
                indexed += index_source_embeddings(source.id)
        bump_corpus_revision(options["namespace"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Reindexed {sources.count()} source(s), {indexed} embedding(s)."
            )
        )
