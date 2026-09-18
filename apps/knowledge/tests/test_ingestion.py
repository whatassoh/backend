import pytest

from apps.knowledge.application.services import (
    KnowledgeIngestionService,
    KnowledgeSourceInput,
)
from apps.knowledge.domain.enums import KnowledgeSourceStatus


@pytest.mark.django_db
def test_ingestion_replaces_chunks_without_duplication():
    service = KnowledgeIngestionService()
    source = service.ingest(
        KnowledgeSourceInput(
            key="delivery",
            title="Livraison",
            source_type="GUIDE",
            content="Premier contenu.\n\nDeuxième contenu.",
        )
    )
    original_count = source.chunks.count()
    source = service.ingest(
        KnowledgeSourceInput(
            key="delivery",
            title="Livraison",
            source_type="GUIDE",
            content="Contenu remplacé.",
        )
    )

    assert source.chunks.count() == 1
    assert original_count >= 1


@pytest.mark.django_db
def test_new_version_deactivates_previous_version():
    service = KnowledgeIngestionService()
    old = service.ingest(
        KnowledgeSourceInput(
            key="cancel", title="Annulation", source_type="FAQ", content="Version un."
        )
    )
    current = service.ingest(
        KnowledgeSourceInput(
            key="cancel",
            title="Annulation",
            source_type="FAQ",
            version=2,
            content="Version deux.",
        )
    )
    old.refresh_from_db()

    assert old.status == KnowledgeSourceStatus.INACTIVE
    assert current.status == KnowledgeSourceStatus.ACTIVE
