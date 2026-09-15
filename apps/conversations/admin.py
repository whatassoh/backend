from django.contrib import admin

from apps.conversations.infrastructure.models import Conversation, ConversationMessage


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
