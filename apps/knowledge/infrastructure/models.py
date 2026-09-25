from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

from apps.knowledge.domain.enums import (
    KnowledgeAudience,
    KnowledgeEmbeddingStatus,
    KnowledgeGrantPrincipal,
    KnowledgeSensitivity,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    KnowledgeTrustLevel,
)
from common.models import BaseModel


class KnowledgeSource(BaseModel):
    namespace = models.SlugField(max_length=80, default="customer-help")
    key = models.SlugField(max_length=100)
    title = models.CharField(max_length=255)
    source_type = models.CharField(max_length=20, choices=KnowledgeSourceType.choices)
    status = models.CharField(
        max_length=20,
        choices=KnowledgeSourceStatus.choices,
        default=KnowledgeSourceStatus.ACTIVE,
    )
    language = models.CharField(max_length=10, default="fr")
    version = models.PositiveIntegerField(default=1)
    content = models.TextField()
    content_hash = models.CharField(max_length=64, default="")
    canonical_url = models.URLField(max_length=500, blank=True, null=True)
    audience = models.CharField(
        max_length=20,
        choices=KnowledgeAudience.choices,
        default=KnowledgeAudience.PUBLIC,
    )
    trust_level = models.CharField(
        max_length=20,
        choices=KnowledgeTrustLevel.choices,
        default=KnowledgeTrustLevel.REVIEWED,
    )
    sensitivity = models.CharField(
        max_length=20,
        choices=KnowledgeSensitivity.choices,
        default=KnowledgeSensitivity.PUBLIC,
    )
    valid_from = models.DateTimeField(blank=True, null=True)
    valid_until = models.DateTimeField(blank=True, null=True)
    security_report = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["key", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["namespace", "key", "version"],
                name="knowledge_source_ns_key_ver_unique",
            ),
            models.UniqueConstraint(
                fields=["namespace", "key"],
                condition=models.Q(status=KnowledgeSourceStatus.ACTIVE),
                name="knowledge_source_one_active_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="knowledge_source_valid_window_check",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(sensitivity=KnowledgeSensitivity.PUBLIC)
                    | models.Q(
                        audience__in=[
                            KnowledgeAudience.RESTRICTED,
                            KnowledgeAudience.INTERNAL,
                        ]
                    )
                ),
                name="knowledge_source_sensitive_audience_check",
            ),
        ]
        indexes = [
            models.Index(
                fields=["namespace", "key", "status"],
                name="knowledge_src_ns_key_stat_idx",
            ),
            models.Index(
                fields=["namespace", "status", "language", "audience"],
                name="knowledge_src_scope_idx",
            ),
            models.Index(fields=["valid_until"], name="knowledge_src_expiry_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.namespace}/{self.key}:v{self.version}"


class KnowledgeAccessGrant(BaseModel):
    """Explicit source grant; authorization is still evaluated server-side."""

    source = models.ForeignKey(
        KnowledgeSource, on_delete=models.CASCADE, related_name="access_grants"
    )
    principal_type = models.CharField(
        max_length=12, choices=KnowledgeGrantPrincipal.choices
    )
    principal_id = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "principal_type", "principal_id"],
                name="knowledge_grant_principal_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["principal_type", "principal_id"],
                name="knowledge_grant_lookup_idx",
            )
        ]


class KnowledgeChunk(BaseModel):
    source = models.ForeignKey(
        KnowledgeSource, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    content_hash = models.CharField(max_length=64, default="")
    section_path = models.CharField(max_length=500, blank=True, default="")
    token_count = models.PositiveIntegerField(default=0)
    search_vector = SearchVectorField(blank=True, null=True, editable=False)
    injection_risk = models.DecimalField(max_digits=4, decimal_places=3, default=0)
    embedding_status = models.CharField(
        max_length=12,
        choices=KnowledgeEmbeddingStatus.choices,
        default=KnowledgeEmbeddingStatus.PENDING,
    )
    embedding_model = models.CharField(max_length=100, blank=True, default="")
    embedding_content_hash = models.CharField(max_length=64, blank=True, default="")
    embedding_attempts = models.PositiveSmallIntegerField(default=0)
    embedding_error = models.CharField(max_length=80, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["source_id", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chunk_index"],
                name="knowledge_chunk_source_index_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(injection_risk__gte=0) & models.Q(injection_risk__lte=1)
                ),
                name="knowledge_chunk_risk_range_check",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source", "chunk_index"], name="knowledge_chunk_source_idx"
            ),
            models.Index(
                fields=["embedding_status", "updated_at"],
                name="knowledge_chunk_embed_idx",
            ),
            GinIndex(fields=["search_vector"], name="knowledge_chunk_fts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source} #{self.chunk_index}"


class KnowledgeRetrievalAudit(BaseModel):
    """Content-free RAG trace for security, quality and latency monitoring."""

    query_hash = models.CharField(max_length=64)
    acl_fingerprint = models.CharField(max_length=64)
    namespace = models.SlugField(max_length=80)
    language = models.CharField(max_length=10)
    strategy = models.CharField(max_length=30)
    chunk_ids = models.JSONField(default=list)
    source_ids = models.JSONField(default=list)
    candidate_count = models.PositiveSmallIntegerField(default=0)
    cache_hit = models.BooleanField(default=False)
    grounded = models.BooleanField(blank=True, null=True)
    no_answer_reason = models.CharField(max_length=40, blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)
    correlation_id = models.CharField(max_length=128, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"], name="knowledge_audit_created_idx"),
            models.Index(
                fields=["namespace", "strategy", "created_at"],
                name="knowledge_audit_strategy_idx",
            ),
        ]
