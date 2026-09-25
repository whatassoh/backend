from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count

from apps.knowledge.infrastructure.models import KnowledgeChunk, KnowledgeSource


class Command(BaseCommand):
    help = "Report corpus lifecycle and embedding status without document content."

    def handle(self, *args, **options):
        source_counts = KnowledgeSource.objects.values("status").annotate(
            total=Count("id")
        )
        chunk_counts = KnowledgeChunk.objects.values("embedding_status").annotate(
            total=Count("id")
        )
        extension_versions = {"pg_trgm": "unavailable", "vector": "unavailable"}
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT extname, extversion FROM pg_extension "
                    "WHERE extname IN ('pg_trgm', 'vector')"
                )
                extension_versions = {
                    "pg_trgm": "not-installed",
                    "vector": "not-installed",
                    **dict(cursor.fetchall()),
                }
        self.stdout.write(f"pg_trgm={extension_versions['pg_trgm']}")
        self.stdout.write(f"pgvector={extension_versions['vector']}")
        for row in source_counts.order_by("status"):
            self.stdout.write(f"sources.{row['status'].lower()}={row['total']}")
        for row in chunk_counts.order_by("embedding_status"):
            self.stdout.write(
                f"chunks.{row['embedding_status'].lower()}={row['total']}"
            )
