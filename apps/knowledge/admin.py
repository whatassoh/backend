from django.contrib import admin

from apps.knowledge.infrastructure.models import KnowledgeChunk, KnowledgeSource


class KnowledgeChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    extra = 0
    can_delete = False
    readonly_fields = ["chunk_index", "content", "metadata", "created_at", "updated_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeSource)
class KnowledgeSourceAdmin(admin.ModelAdmin):
    list_display = ["key", "title", "version", "status", "language", "source_type"]
    list_filter = ["status", "language", "source_type"]
    search_fields = ["key", "title", "content"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [KnowledgeChunkInline]


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
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
