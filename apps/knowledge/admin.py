from django.contrib import admin

from apps.knowledge.infrastructure.models import (
    KnowledgeAccessGrant,
    KnowledgeChunk,
    KnowledgeRetrievalAudit,
    KnowledgeSource,
)


class KnowledgeChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    extra = 0
    can_delete = False
    readonly_fields = [
        "chunk_index",
        "section_path",
        "content",
        "content_hash",
        "token_count",
        "injection_risk",
        "embedding_status",
        "embedding_model",
        "embedding_attempts",
        "embedding_error",
        "metadata",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeSource)
class KnowledgeSourceAdmin(admin.ModelAdmin):
    list_display = [
        "namespace",
        "key",
        "title",
        "version",
        "status",
        "audience",
        "language",
        "source_type",
    ]
    list_filter = ["namespace", "status", "audience", "language", "source_type"]
    search_fields = ["key", "title", "content"]
    readonly_fields = [field.name for field in KnowledgeSource._meta.fields]
    inlines = [KnowledgeChunkInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ["source", "chunk_index", "created_at"]
    search_fields = ["content", "source__key", "source__title"]
    readonly_fields = [
        "id",
        "source",
        "chunk_index",
        "content",
        "metadata",
        "content_hash",
        "section_path",
        "token_count",
        "injection_risk",
        "embedding_status",
        "embedding_model",
        "embedding_attempts",
        "embedding_error",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeAccessGrant)
class KnowledgeAccessGrantAdmin(admin.ModelAdmin):
    list_display = ["source", "principal_type", "principal_id", "created_at"]
    list_filter = ["principal_type"]
    search_fields = ["source__key", "principal_id"]
    readonly_fields = [field.name for field in KnowledgeAccessGrant._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeRetrievalAudit)
class KnowledgeRetrievalAuditAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "namespace",
        "strategy",
        "candidate_count",
        "cache_hit",
        "grounded",
        "no_answer_reason",
        "latency_ms",
    ]
    list_filter = ["namespace", "strategy", "cache_hit", "grounded"]
    readonly_fields = [field.name for field in KnowledgeRetrievalAudit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
