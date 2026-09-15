from django.contrib import admin

from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent


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
