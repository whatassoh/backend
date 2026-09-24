from django.contrib import admin

from apps.conversations.infrastructure.models import (
    Conversation,
    ConversationEpisode,
    ConversationMessage,
    ConversationStyleProfile,
    ConversationSummary,
    MemoryFact,
    UserCommunicationProfile,
)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "channel",
        "channel_identity",
        "status",
        "external_chat_id",
        "started_at",
        "last_activity_at",
        "closed_at",
    ]
    list_filter = ["channel", "status", "created_at"]
    search_fields = [
        "id",
        "external_chat_id",
        "user__whatsapp_phone",
        "channel_identity__external_user_id",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "last_activity_at",
        "closed_at",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(["user", "channel", "channel_identity", "external_chat_id"])
        return fields


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    list_display = [
        "conversation",
        "direction",
        "message_type",
        "text_preview",
        "occurred_at",
    ]
    list_filter = ["direction", "message_type", "occurred_at"]
    search_fields = ["text", "conversation__external_chat_id"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "location",
        "metadata",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(
                [
                    "conversation",
                    "message_event",
                    "direction",
                    "message_type",
                    "text",
                    "occurred_at",
                ]
            )
        return fields


class _ReadOnlyMemoryAdmin(admin.ModelAdmin):
    """Memory records are changed only through audited application services."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConversationStyleProfile)
class ConversationStyleProfileAdmin(_ReadOnlyMemoryAdmin):
    list_display = [
        "conversation",
        "language",
        "register",
        "address_form",
        "sample_count",
    ]
    search_fields = ["conversation__id", "conversation__user__whatsapp_phone"]


@admin.register(UserCommunicationProfile)
class UserCommunicationProfileAdmin(_ReadOnlyMemoryAdmin):
    list_display = ["user", "language", "register", "address_form", "sample_count"]
    search_fields = ["user__whatsapp_phone", "user__first_name"]


@admin.register(ConversationSummary)
class ConversationSummaryAdmin(_ReadOnlyMemoryAdmin):
    list_display = ["conversation", "prompt_version", "status", "created_at"]
    list_filter = ["status", "prompt_version"]
    search_fields = ["conversation__id", "content_hash"]


@admin.register(ConversationEpisode)
class ConversationEpisodeAdmin(_ReadOnlyMemoryAdmin):
    list_display = ["conversation", "topic", "outcome", "status", "created_at"]
    list_filter = ["status", "topic", "outcome"]


@admin.register(MemoryFact)
class MemoryFactAdmin(_ReadOnlyMemoryAdmin):
    list_display = ["user", "fact_type", "consent", "expires_at", "deleted_at"]
    list_filter = ["fact_type", "consent", "sensitivity"]
    search_fields = ["user__whatsapp_phone", "conversation__id"]
