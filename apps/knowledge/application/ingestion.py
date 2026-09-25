"""Validated, versioned and ACL-aware knowledge ingestion."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib.postgres.search import SearchVector
from django.db import connection, transaction
from django.db.models import Value
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.identity.domain.enums import UserRoleType
from apps.knowledge.application.cache import bump_corpus_revision
from apps.knowledge.application.chunking import DocumentChunker, normalize_document
from apps.knowledge.application.observability import observe_ingestion
from apps.knowledge.application.retrieval import LANGUAGE_CONFIGS
from apps.knowledge.application.security import safe_evidence_text, scan_document
from apps.knowledge.domain.enums import (
    KnowledgeAudience,
    KnowledgeEmbeddingStatus,
    KnowledgeGrantPrincipal,
    KnowledgeSensitivity,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    KnowledgeTrustLevel,
)
from apps.knowledge.domain.exceptions import (
    KnowledgeIngestionRejected,
    KnowledgeValidationError,
)
from apps.knowledge.infrastructure.models import (
    KnowledgeAccessGrant,
    KnowledgeChunk,
    KnowledgeSource,
)

logger = logging.getLogger(__name__)
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LANGUAGE_RE = re.compile(r"^(?:[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8})?|und)$")


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeGrantInput:
    principal_type: str
    principal_id: str


@dataclass(frozen=True, kw_only=True)
class KnowledgeSourceInput:
    key: str
    title: str
    content: str
    source_type: str
    language: str = "fr"
    version: int = 1
    namespace: str = "customer-help"
    audience: str = KnowledgeAudience.PUBLIC
    trust_level: str = KnowledgeTrustLevel.REVIEWED
    sensitivity: str = KnowledgeSensitivity.PUBLIC
    canonical_url: str | None = None
    valid_from: object | None = None
    valid_until: object | None = None
    grants: tuple[KnowledgeGrantInput, ...] | list[dict] = ()
    metadata: dict | None = None


class KnowledgeIngestionService:
    """Promote only policy-compliant sources and invalidate retrieval caches."""

    def __init__(self, *, chunker=None) -> None:
        self.chunker = chunker or DocumentChunker(
            target_characters=settings.AI_RAG_CHUNK_TARGET_CHARACTERS,
            max_characters=settings.AI_RAG_CHUNK_MAX_CHARACTERS,
            overlap_characters=settings.AI_RAG_CHUNK_OVERLAP_CHARACTERS,
        )

    @transaction.atomic
    def ingest(self, source_input: KnowledgeSourceInput) -> KnowledgeSource:
        normalized = self._validate(source_input)
        normalized_title = safe_evidence_text(source_input.title, maximum=255)
        metadata = source_input.metadata or {}
        serialized_metadata = json.dumps(metadata, sort_keys=True, allow_nan=False)
        report = scan_document(
            normalized_title + "\n" + normalized + "\n" + serialized_metadata,
            audience=source_input.audience,
            trust_level=source_input.trust_level,
            public_contact_data=bool(metadata.get("public_contact_data_reviewed")),
            instructional_security_content=bool(
                metadata.get("instructional_security_content_reviewed")
            ),
        )
        if report.secret_categories:
            observe_ingestion(status="rejected", source_type=source_input.source_type)
            raise KnowledgeIngestionRejected(
                "Document rejected because secret material was detected."
            )
        if source_input.trust_level == KnowledgeTrustLevel.EXTERNAL:
            report = replace(
                report,
                accepted=False,
                quarantined=True,
                risk_score=max(report.risk_score, 0.25),
                reasons=tuple(
                    dict.fromkeys((*report.reasons, "EXTERNAL_REVIEW_REQUIRED"))
                ),
            )

        status = (
            KnowledgeSourceStatus.QUARANTINED
            if report.quarantined
            or source_input.trust_level == KnowledgeTrustLevel.EXTERNAL
            else KnowledgeSourceStatus.ACTIVE
        )
        valid_from, valid_until = self._validity(source_input)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        self._lock_source_family(source_input.namespace, source_input.key)
        current_active = (
            KnowledgeSource.objects.select_for_update()
            .filter(
                namespace=source_input.namespace,
                key=source_input.key,
                status=KnowledgeSourceStatus.ACTIVE,
            )
            .order_by("-version")
            .first()
        )
        if current_active and source_input.version < current_active.version:
            raise KnowledgeValidationError(
                "An older knowledge version cannot replace the active version."
            )
        if (
            current_active
            and current_active.version == source_input.version
            and status != KnowledgeSourceStatus.ACTIVE
        ):
            raise KnowledgeIngestionRejected(
                "A quarantined replacement cannot overwrite an active version; "
                "submit it with a newer version."
            )
        if status == KnowledgeSourceStatus.ACTIVE:
            KnowledgeSource.objects.filter(
                namespace=source_input.namespace,
                key=source_input.key,
                status=KnowledgeSourceStatus.ACTIVE,
            ).exclude(version=source_input.version).update(
                status=KnowledgeSourceStatus.INACTIVE
            )
        source, _ = KnowledgeSource.objects.select_for_update().update_or_create(
            namespace=source_input.namespace,
            key=source_input.key,
            version=source_input.version,
            defaults={
                "title": normalized_title,
                "content": normalized,
                "content_hash": digest,
                "canonical_url": source_input.canonical_url or None,
                "source_type": source_input.source_type,
                "language": source_input.language.lower().replace("_", "-")[:10],
                "status": status,
                "audience": source_input.audience,
                "trust_level": source_input.trust_level,
                "sensitivity": source_input.sensitivity,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "security_report": report.as_dict(),
                "metadata": metadata,
            },
        )
        source.access_grants.all().delete()
        grants = self._grants(source_input.grants)
        KnowledgeAccessGrant.objects.bulk_create(
            [
                KnowledgeAccessGrant(
                    source=source,
                    principal_type=grant.principal_type,
                    principal_id=grant.principal_id,
                )
                for grant in grants
            ]
        )
        source.chunks.all().delete()
        if status == KnowledgeSourceStatus.ACTIVE:
            chunks = self.chunker.split(normalized)
            if not chunks:
                raise KnowledgeValidationError("Document produced no indexable chunks.")
            embedding_status = (
                KnowledgeEmbeddingStatus.PENDING
                if settings.AI_RAG_SEMANTIC_ENABLED
                else KnowledgeEmbeddingStatus.SKIPPED
            )
            KnowledgeChunk.objects.bulk_create(
                [
                    KnowledgeChunk(
                        source=source,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        content_hash=chunk.content_hash,
                        section_path=chunk.section_path,
                        token_count=chunk.token_count,
                        injection_risk=report.risk_score,
                        embedding_status=embedding_status,
                    )
                    for chunk in chunks
                ]
            )
            self._refresh_search_vectors(source)
            if settings.AI_RAG_SEMANTIC_ENABLED:
                transaction.on_commit(lambda: self._enqueue_embeddings(source.id))
        transaction.on_commit(lambda: bump_corpus_revision(source.namespace))
        observe_ingestion(status=status, source_type=source.source_type)
        return source

    @staticmethod
    def _validate(source_input: KnowledgeSourceInput) -> str:
        if not isinstance(source_input.key, str) or not SLUG_RE.fullmatch(
            source_input.key
        ):
            raise KnowledgeValidationError("Knowledge key must be a lowercase slug.")
        if not isinstance(source_input.namespace, str) or not SLUG_RE.fullmatch(
            source_input.namespace
        ):
            raise KnowledgeValidationError(
                "Knowledge namespace must be a lowercase slug."
            )
        if not isinstance(source_input.title, str):
            raise KnowledgeValidationError("Knowledge title is invalid.")
        normalized_title = safe_evidence_text(source_input.title, maximum=256)
        if not normalized_title or len(normalized_title) > 255:
            raise KnowledgeValidationError("Knowledge title is invalid.")
        if source_input.source_type not in KnowledgeSourceType.values:
            raise KnowledgeValidationError("Unknown knowledge source type.")
        if source_input.audience not in KnowledgeAudience.values:
            raise KnowledgeValidationError("Unknown knowledge audience.")
        if source_input.trust_level not in KnowledgeTrustLevel.values:
            raise KnowledgeValidationError("Unknown knowledge trust level.")
        if source_input.sensitivity not in KnowledgeSensitivity.values:
            raise KnowledgeValidationError("Unknown knowledge sensitivity.")
        if (
            not isinstance(source_input.version, int)
            or isinstance(source_input.version, bool)
            or source_input.version < 1
        ):
            raise KnowledgeValidationError("Knowledge version must be positive.")
        if not isinstance(source_input.language, str) or not LANGUAGE_RE.fullmatch(
            source_input.language
        ):
            raise KnowledgeValidationError("Knowledge language is invalid.")
        if source_input.sensitivity != KnowledgeSensitivity.PUBLIC and (
            source_input.audience
            not in {KnowledgeAudience.RESTRICTED, KnowledgeAudience.INTERNAL}
        ):
            raise KnowledgeValidationError(
                "Sensitive knowledge must be restricted or internal."
            )
        if (
            source_input.audience == KnowledgeAudience.PUBLIC
            and source_input.source_type == KnowledgeSourceType.OPERATIONS
        ):
            raise KnowledgeValidationError("Operational knowledge cannot be public.")
        if source_input.canonical_url:
            if not isinstance(source_input.canonical_url, str):
                raise KnowledgeValidationError("Knowledge canonical URL is invalid.")
            parsed_url = urlsplit(source_input.canonical_url)
            if (
                parsed_url.scheme != "https"
                or not parsed_url.hostname
                or parsed_url.username
                or parsed_url.password
            ):
                raise KnowledgeValidationError(
                    "Knowledge canonical URL must be an HTTPS public reference."
                )
        if source_input.metadata is not None and not isinstance(
            source_input.metadata, dict
        ):
            raise KnowledgeValidationError("Knowledge metadata must be an object.")
        metadata = source_input.metadata or {}
        if any(not isinstance(key, str) for key in metadata):
            raise KnowledgeValidationError("Knowledge metadata keys must be strings.")
        if metadata.get("dynamic_business_data"):
            raise KnowledgeIngestionRejected(
                "Live price, stock, order and location data must use business tools."
            )
        try:
            serialized_metadata = json.dumps(metadata, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise KnowledgeValidationError("Knowledge metadata must be JSON.") from exc
        if len(serialized_metadata) > settings.AI_RAG_MAX_METADATA_CHARACTERS:
            raise KnowledgeValidationError("Knowledge metadata is too large.")
        if not isinstance(source_input.content, str):
            raise KnowledgeValidationError("Knowledge content must be text.")
        normalized = normalize_document(source_input.content)
        if not normalized:
            raise KnowledgeValidationError("Knowledge content cannot be empty.")
        if len(normalized) > settings.AI_RAG_MAX_SOURCE_CHARACTERS:
            raise KnowledgeValidationError("Knowledge source exceeds the size limit.")
        grants = KnowledgeIngestionService._grants(source_input.grants)
        if source_input.audience == KnowledgeAudience.RESTRICTED and not grants:
            raise KnowledgeValidationError("Restricted knowledge requires a grant.")
        if source_input.audience != KnowledgeAudience.RESTRICTED and grants:
            raise KnowledgeValidationError("Only restricted knowledge accepts grants.")
        return normalized

    @staticmethod
    def _grants(values) -> tuple[KnowledgeGrantInput, ...]:
        try:
            grants = tuple(
                value
                if isinstance(value, KnowledgeGrantInput)
                else KnowledgeGrantInput(**value)
                for value in (values or ())
            )
        except (TypeError, ValueError) as exc:
            raise KnowledgeValidationError("Malformed knowledge grant.") from exc
        seen = set()
        for grant in grants:
            if grant.principal_type not in KnowledgeGrantPrincipal.values:
                raise KnowledgeValidationError("Unknown knowledge grant principal.")
            if not grant.principal_id or len(grant.principal_id) > 64:
                raise KnowledgeValidationError("Invalid knowledge grant identifier.")
            marker = (grant.principal_type, grant.principal_id)
            if marker in seen:
                raise KnowledgeValidationError("Duplicate knowledge grant.")
            seen.add(marker)
            if grant.principal_type == KnowledgeGrantPrincipal.ROLE:
                if grant.principal_id not in UserRoleType.values:
                    raise KnowledgeValidationError("Unknown role knowledge grant.")
            else:
                try:
                    UUID(grant.principal_id)
                except ValueError as exc:
                    raise KnowledgeValidationError(
                        "User and store grants require UUID identifiers."
                    ) from exc
        return grants

    @staticmethod
    def _validity(source_input) -> tuple[datetime | None, datetime | None]:
        def parse(value):
            if value is None or value == "":
                return None
            parsed = (
                value if isinstance(value, datetime) else parse_datetime(str(value))
            )
            if parsed is None:
                raise KnowledgeValidationError("Invalid knowledge validity datetime.")
            return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

        valid_from = parse(source_input.valid_from)
        valid_until = parse(source_input.valid_until)
        if valid_from and valid_until and valid_until <= valid_from:
            raise KnowledgeValidationError(
                "Knowledge valid_until must be after valid_from."
            )
        return valid_from, valid_until

    @staticmethod
    def _refresh_search_vectors(source) -> None:
        if connection.vendor != "postgresql":
            return
        config = LANGUAGE_CONFIGS.get(source.language.split("-", 1)[0], "simple")
        KnowledgeChunk.objects.filter(source=source).update(
            search_vector=(
                SearchVector(Value(source.title), config=config, weight="A")
                + SearchVector("content", config=config, weight="B")
                + SearchVector("section_path", config=config, weight="A")
            )
        )

    @staticmethod
    def _lock_source_family(namespace: str, key: str) -> None:
        """Serialize version promotion, including the first concurrent insert."""

        if connection.vendor == "postgresql":
            material = f"{namespace}\x1f{key}".encode()
            lock_id = int.from_bytes(
                hashlib.sha256(material).digest()[:8], "big", signed=True
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
            return
        # SQLite serializes writes; locking existing rows keeps other supported
        # transactional databases consistent for non-empty source families.
        list(
            KnowledgeSource.objects.select_for_update()
            .filter(namespace=namespace, key=key)
            .values_list("id", flat=True)
        )

    @staticmethod
    def _enqueue_embeddings(source_id) -> None:
        try:
            from apps.knowledge.tasks import index_knowledge_source

            index_knowledge_source.delay(str(source_id))
        except Exception:
            logger.exception("knowledge_embedding_enqueue_failed")
