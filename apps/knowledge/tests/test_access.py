from uuid import uuid4

import pytest

from apps.knowledge.application.access import visible_sources
from apps.knowledge.domain.dtos import KnowledgePrincipal
from apps.knowledge.infrastructure.models import KnowledgeAccessGrant, KnowledgeSource


@pytest.mark.django_db
def test_restricted_source_never_leaks_to_another_user():
    owner_id = uuid4()
    other_id = uuid4()
    source = KnowledgeSource.objects.create(
        namespace="customer-help",
        key="private-guide",
        title="Private",
        source_type="GUIDE",
        status="ACTIVE",
        audience="RESTRICTED",
        content="Private content",
    )
    KnowledgeAccessGrant.objects.create(
        source=source, principal_type="USER", principal_id=str(owner_id)
    )

    owner_sources = visible_sources(
        principal=KnowledgePrincipal(user_id=owner_id),
        namespace="customer-help",
        language="fr",
    )
    other_sources = visible_sources(
        principal=KnowledgePrincipal(user_id=other_id),
        namespace="customer-help",
        language="fr",
    )

    assert owner_sources.filter(id=source.id).exists()
    assert not other_sources.filter(id=source.id).exists()


@pytest.mark.django_db
def test_internal_source_requires_staff_and_explicit_internal_context():
    source = KnowledgeSource.objects.create(
        namespace="customer-help",
        key="internal-guide",
        title="Internal",
        source_type="OPERATIONS",
        status="ACTIVE",
        audience="INTERNAL",
        sensitivity="INTERNAL",
        content="Internal content",
    )

    denied = visible_sources(
        principal=KnowledgePrincipal(is_staff=True, allow_internal=False),
        namespace="customer-help",
        language="fr",
    )
    allowed = visible_sources(
        principal=KnowledgePrincipal(is_staff=True, allow_internal=True),
        namespace="customer-help",
        language="fr",
    )

    assert not denied.filter(id=source.id).exists()
    assert allowed.filter(id=source.id).exists()
