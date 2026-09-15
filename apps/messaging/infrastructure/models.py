from django.conf import settings
from django.db import models

from apps.messaging.domain.enums import (
    MessageDirection,
    MessageStatus,
    MessageType,
    MessagingChannel,
)
from common.models import BaseModel


class ChannelIdentity(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="channel_identities",
    )
    channel = models.CharField(max_length=20, choices=MessagingChannel.choices)
    external_user_id = models.CharField(max_length=255)
    external_username = models.CharField(max_length=255, blank=True, null=True)
    phone_number = models.CharField(max_length=32, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user_id", "channel", "external_user_id"]
        indexes = [
            models.Index(
                fields=["user", "channel"], name="msg_identity_user_channel_idx"
            ),
            models.Index(
                fields=["channel", "external_user_id"], name="msg_identity_external_idx"
            ),
            models.Index(fields=["is_active"], name="msg_identity_active_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_user_id"],
                name="msg_identity_channel_external_unique",
            ),
            models.CheckConstraint(
                name="msg_identity_channel_valid",
                condition=models.Q(channel__in=MessagingChannel.values),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.external_user_id}"


class MessageEvent(BaseModel):
    channel = models.CharField(max_length=20, choices=MessagingChannel.choices)
    direction = models.CharField(max_length=20, choices=MessageDirection.choices)
    external_message_id = models.CharField(max_length=255, blank=True, null=True)
    external_user_id = models.CharField(max_length=255)
    external_chat_id = models.CharField(max_length=255, blank=True, null=True)
    message_type = models.CharField(max_length=20, choices=MessageType.choices)
    status = models.CharField(max_length=20, choices=MessageStatus.choices)
    raw_payload = models.JSONField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["channel", "external_message_id"], name="msg_event_external_idx"
            ),
            models.Index(fields=["external_user_id"], name="msg_event_user_idx"),
            models.Index(fields=["external_chat_id"], name="msg_event_chat_idx"),
            models.Index(fields=["status"], name="msg_event_status_idx"),
            models.Index(fields=["created_at"], name="msg_event_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_message_id"],
                condition=models.Q(external_message_id__isnull=False),
                name="msg_event_channel_external_unique",
            ),
            models.CheckConstraint(
                name="msg_event_channel_valid",
                condition=models.Q(channel__in=MessagingChannel.values),
            ),
            models.CheckConstraint(
                name="msg_event_direction_valid",
                condition=models.Q(direction__in=MessageDirection.values),
            ),
            models.CheckConstraint(
                name="msg_event_type_valid",
                condition=models.Q(message_type__in=MessageType.values),
            ),
            models.CheckConstraint(
                name="msg_event_status_valid",
                condition=models.Q(status__in=MessageStatus.values),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.direction}:{self.external_message_id or self.id}"
