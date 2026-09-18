from django.db import models

from apps.knowledge.domain.enums import KnowledgeSourceStatus, KnowledgeSourceType
from common.models import BaseModel


class KnowledgeSource(BaseModel):
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
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["key", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["key", "version"], name="knowledge_source_key_version_unique"
            )
        ]
        indexes = [
            models.Index(
                fields=["key", "status"], name="knowledge_src_key_status_idx"
            ),
            models.Index(
                fields=["status", "language"], name="knowledge_src_status_lang_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key}:v{self.version}"


class KnowledgeChunk(BaseModel):
    source = models.ForeignKey(
        KnowledgeSource, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["source_id", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chunk_index"],
                name="knowledge_chunk_source_index_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["source", "chunk_index"], name="knowledge_chunk_source_idx"
            )
        ]

    def __str__(self) -> str:
        return f"{self.source} #{self.chunk_index}"
