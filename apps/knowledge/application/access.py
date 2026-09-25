"""Knowledge authorization resolved before lexical or semantic retrieval."""

import hashlib
import hmac

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.knowledge.domain.dtos import KnowledgePrincipal
from apps.knowledge.domain.enums import (
    KnowledgeAudience,
    KnowledgeGrantPrincipal,
    KnowledgeSourceStatus,
)
from apps.knowledge.infrastructure.models import KnowledgeAccessGrant, KnowledgeSource


def principal_for_user(user) -> KnowledgePrincipal:
    if user is None or getattr(user, "id", None) is None:
        return KnowledgePrincipal()
    roles = tuple(sorted(user.roles.values_list("role", flat=True).distinct()))
    store_ids = tuple(
        user.store_memberships.filter(status="ACTIVE")
        .order_by("store_id")
        .values_list("store_id", flat=True)
        .distinct()
    )
    return KnowledgePrincipal(
        user_id=user.id,
        roles=roles,
        store_ids=store_ids,
        is_staff=bool(user.is_staff),
    )


def acl_fingerprint(principal: KnowledgePrincipal) -> str:
    values = [
        str(principal.user_id or "anonymous"),
        *sorted(principal.roles),
        *(str(value) for value in sorted(principal.store_ids, key=str)),
        str(principal.is_staff),
        str(principal.allow_internal),
    ]
    return hmac.new(
        settings.SECRET_KEY.encode(),
        "\x1f".join(values).encode(),
        hashlib.sha256,
    ).hexdigest()


def visible_sources(*, principal: KnowledgePrincipal, namespace: str, language: str):
    now = timezone.now()
    sources = KnowledgeSource.objects.filter(
        namespace=namespace,
        status=KnowledgeSourceStatus.ACTIVE,
        language__in=_language_candidates(language),
    ).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=now))
    sources = sources.filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))

    audience = Q(audience=KnowledgeAudience.PUBLIC)
    if principal.user_id:
        audience |= Q(audience=KnowledgeAudience.AUTHENTICATED)
        grants = Q(
            principal_type=KnowledgeGrantPrincipal.USER,
            principal_id=str(principal.user_id),
        )
        if principal.roles:
            grants |= Q(
                principal_type=KnowledgeGrantPrincipal.ROLE,
                principal_id__in=principal.roles,
            )
        if principal.store_ids:
            grants |= Q(
                principal_type=KnowledgeGrantPrincipal.STORE,
                principal_id__in=[str(value) for value in principal.store_ids],
            )
        matching_grants = KnowledgeAccessGrant.objects.filter(
            source_id=OuterRef("pk")
        ).filter(grants)
        sources = sources.annotate(_has_grant=Exists(matching_grants))
        audience |= Q(audience=KnowledgeAudience.RESTRICTED, _has_grant=True)
    if principal.is_staff and principal.allow_internal:
        audience |= Q(audience=KnowledgeAudience.INTERNAL)
    return sources.filter(audience).distinct()


def _language_candidates(language: str) -> tuple[str, ...]:
    normalized = (language or "fr").lower().replace("_", "-")
    base = normalized.split("-", 1)[0]
    return tuple(dict.fromkeys((normalized[:10], base[:10], "und")))
