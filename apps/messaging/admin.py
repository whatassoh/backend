from django.contrib import admin

from apps.messaging.application.reliable_pipeline import (
    replay_inbox_dead_letter,
    replay_outbox_dead_letter,
)
from apps.messaging.infrastructure.models import (
    ChannelIdentity,
    ConversationInboxItem,
    ConversationOutboxItem,
    MessageEvent,
)


@admin.register(ChannelIdentity)
class ChannelIdentityAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "channel",
        "external_user_id",
        "external_username",
        "phone_number",
        "is_active",
        "created_at",
    ]
    list_filter = ["channel", "is_active"]
    search_fields = [
        "external_user_id",
        "external_username",
        "phone_number",
        "user__whatsapp_phone",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(["user", "channel", "external_user_id"])
        return fields


@admin.register(MessageEvent)
class MessageEventAdmin(admin.ModelAdmin):
    list_display = [
        "channel",
        "direction",
        "external_message_id",
        "external_user_id",
        "message_type",
        "status",
        "created_at",
        "processed_at",
    ]
    list_filter = ["channel", "direction", "message_type", "status"]
    search_fields = [
        "external_message_id",
        "external_user_id",
        "external_chat_id",
    ]
    readonly_fields = [
        "id",
        "raw_payload",
        "created_at",
        "updated_at",
        "received_at",
        "sent_at",
        "processed_at",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(
                [
                    "channel",
                    "direction",
                    "external_message_id",
                    "external_user_id",
                    "external_chat_id",
                    "message_type",
                    "status",
                    "metadata",
                ]
            )
        return fields


@admin.action(description="Rejouer les éléments inbox en DLQ")
def replay_inbox(modeladmin, request, queryset):
    replayed = sum(replay_inbox_dead_letter(item.id) for item in queryset)
    modeladmin.message_user(request, f"{replayed} élément(s) inbox remis en attente.")


@admin.register(ConversationInboxItem)
class ConversationInboxItemAdmin(admin.ModelAdmin):
    list_display = ["conversation", "sequence", "status", "attempts", "available_at"]
    list_filter = ["status"]
    search_fields = ["conversation__id", "message_event__external_message_id"]
    readonly_fields = [field.name for field in ConversationInboxItem._meta.fields]
    actions = [replay_inbox]


@admin.action(description="Rejouer les sorties DLQ sans livraison incertaine")
def replay_outbox(modeladmin, request, queryset):
    replayed = sum(replay_outbox_dead_letter(item.id) for item in queryset)
    modeladmin.message_user(request, f"{replayed} sortie(s) remise(s) en attente.")


@admin.register(ConversationOutboxItem)
class ConversationOutboxItemAdmin(admin.ModelAdmin):
    list_display = ["conversation", "sequence", "status", "attempts", "available_at"]
    list_filter = ["status", "last_error_category"]
    search_fields = ["conversation__id", "idempotency_key"]
    readonly_fields = [field.name for field in ConversationOutboxItem._meta.fields]
    actions = [replay_outbox]
