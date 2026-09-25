from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.messaging.domain.enums import (
    InboxStatus,
    MessageDirection,
    MessageStatus,
    MessageType,
    MessagingChannel,
    NotificationAttemptOutcome,
    NotificationKind,
    NotificationStatus,
    OutboxStatus,
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


class ConversationInboxItem(BaseModel):
    """One durable, ordered inbound turn for a conversation.

    The payload contains only the normalized transport message. Business state
    remains in its authoritative tables and is reloaded by the worker.
    """

    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="inbox_items",
    )
    message_event = models.OneToOneField(
        MessageEvent, on_delete=models.PROTECT, related_name="inbox_item"
    )
    sequence = models.PositiveBigIntegerField()
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=30, choices=InboxStatus.choices, default=InboxStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    fencing_token = models.UUIDField(blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    last_error_category = models.CharField(max_length=80, blank=True, null=True)
    last_error_message = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        ordering = ["conversation_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "sequence"],
                name="msg_inbox_conv_sequence_unique",
            )
        ]
        indexes = [
            models.Index(fields=["status", "available_at"], name="msg_inbox_ready_idx"),
            models.Index(
                fields=["conversation", "sequence", "status"],
                name="msg_inbox_order_idx",
            ),
        ]


class ConversationOutboxItem(BaseModel):
    """A response intent committed atomically with its processed inbound turn."""

    inbox_item = models.OneToOneField(
        ConversationInboxItem, on_delete=models.PROTECT, related_name="outbox_item"
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="outbox_items",
    )
    sequence = models.PositiveBigIntegerField()
    payload = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=180, unique=True)
    status = models.CharField(
        max_length=30, choices=OutboxStatus.choices, default=OutboxStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    fencing_token = models.UUIDField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    last_error_category = models.CharField(max_length=80, blank=True, null=True)
    last_error_message = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        ordering = ["conversation_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "sequence"],
                name="msg_outbox_conv_sequence_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "available_at"], name="msg_outbox_ready_idx"
            ),
            models.Index(
                fields=["conversation", "sequence", "status"],
                name="msg_outbox_order_idx",
            ),
        ]


class OperationalNotification(BaseModel):
    """A DB-backed operational delivery intent, never a business transition."""

    kind = models.CharField(max_length=50, choices=NotificationKind.choices)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel_identity = models.ForeignKey(ChannelIdentity, on_delete=models.PROTECT)
    conversation = models.ForeignKey(
        "conversations.Conversation", on_delete=models.SET_NULL, null=True, blank=True
    )
    channel = models.CharField(max_length=20, choices=MessagingChannel.choices)
    business_entity_type = models.CharField(max_length=80)
    business_entity_id = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=30,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
    )
    idempotency_key = models.CharField(max_length=180, unique=True)
    correlation_id = models.CharField(max_length=100, blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    next_retry_at = models.DateTimeField(blank=True, null=True)
    last_error_category = models.CharField(max_length=40, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"], name="msg_notif_status_idx"),
            models.Index(
                fields=["business_entity_type", "business_entity_id"],
                name="msg_notif_business_idx",
            ),
        ]


class OperationalNotificationAttempt(BaseModel):
    notification = models.ForeignKey(
        OperationalNotification, on_delete=models.CASCADE, related_name="attempts"
    )
    attempt_number = models.PositiveIntegerField()
    channel = models.CharField(max_length=20, choices=MessagingChannel.choices)
    provider = models.CharField(max_length=40)
    outcome = models.CharField(
        max_length=20,
        choices=NotificationAttemptOutcome.choices,
        default=NotificationAttemptOutcome.STARTED,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    error_category = models.CharField(max_length=40, blank=True, null=True)
    retry_after_seconds = models.PositiveIntegerField(blank=True, null=True)
    message_event = models.ForeignKey(
        MessageEvent, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "attempt_number"],
                name="msg_notif_attempt_number_unique",
            )
        ]
