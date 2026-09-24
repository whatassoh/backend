from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.conversations.domain.enums import ConversationStatus
from apps.messaging.domain.enums import MessageDirection, MessageType, MessagingChannel
from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent
from common.models import BaseModel


class Conversation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    channel = models.CharField(max_length=20, choices=MessagingChannel.choices)
    channel_identity = models.ForeignKey(
        ChannelIdentity,
        on_delete=models.PROTECT,
        related_name="conversations",
    )
    external_chat_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=ConversationStatus.choices,
        default=ConversationStatus.ACTIVE,
    )
    context = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-last_activity_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["channel_identity", "external_chat_id", "status"],
                name="conv_identity_chat_status_idx",
            ),
            models.Index(fields=["user", "status"], name="conv_user_status_idx"),
            models.Index(fields=["channel", "status"], name="conv_channel_status_idx"),
            models.Index(fields=["last_activity_at"], name="conv_last_activity_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["channel_identity", "external_chat_id"],
                condition=models.Q(status=ConversationStatus.ACTIVE),
                name="conv_active_identity_chat_unique",
            ),
            models.UniqueConstraint(
                fields=["channel_identity"],
                condition=models.Q(
                    status=ConversationStatus.ACTIVE,
                    external_chat_id__isnull=True,
                ),
                name="conv_active_identity_null_chat_unique",
            ),
            models.CheckConstraint(
                name="conv_channel_valid",
                condition=models.Q(channel__in=MessagingChannel.values),
            ),
            models.CheckConstraint(
                name="conv_status_valid",
                condition=models.Q(status__in=ConversationStatus.values),
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.channel_identity_id and self.channel != self.channel_identity.channel:
            raise ValidationError(
                "Conversation channel must match channel identity channel."
            )

    def save(self, *args: object, **kwargs: object) -> None:
        if self.channel_identity_id:
            self.channel = self.channel_identity.channel
            self.user = self.channel_identity.user
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.channel}:{self.external_chat_id or self.channel_identity_id}"


class ConversationMessage(BaseModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    message_event = models.OneToOneField(
        MessageEvent,
        on_delete=models.SET_NULL,
        related_name="conversation_message",
        blank=True,
        null=True,
    )
    direction = models.CharField(max_length=20, choices=MessageDirection.choices)
    message_type = models.CharField(max_length=20, choices=MessageType.choices)
    text = models.TextField(blank=True, null=True)
    location = models.JSONField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["occurred_at", "created_at", "id"]
        indexes = [
            models.Index(
                fields=["conversation", "occurred_at"],
                name="conv_msg_conversation_time_idx",
            ),
            models.Index(fields=["direction"], name="conv_msg_direction_idx"),
            models.Index(fields=["message_type"], name="conv_msg_type_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="conv_msg_direction_valid",
                condition=models.Q(direction__in=MessageDirection.values),
            ),
            models.CheckConstraint(
                name="conv_msg_type_valid",
                condition=models.Q(message_type__in=MessageType.values),
            ),
        ]

    def text_preview(self) -> str:
        if not self.text:
            return ""
        return self.text[:80]

    def __str__(self) -> str:
        return f"{self.conversation_id}:{self.direction}:{self.message_type}"


class ConversationStyleProfile(BaseModel):
    """Low-sensitivity writing preferences inferred from recent user messages."""

    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="style_profile"
    )
    language = models.CharField(max_length=12, default="fr")
    register = models.CharField(max_length=20, default="neutral")
    address_form = models.CharField(max_length=12, default="vous")
    verbosity = models.CharField(max_length=12, default="balanced")
    warmth = models.CharField(max_length=12, default="warm")
    emoji_level = models.CharField(max_length=12, default="none")
    technical_level = models.CharField(max_length=12, default="simple")
    sample_count = models.PositiveIntegerField(default=0)
    signals = models.JSONField(default=dict, blank=True)


class UserCommunicationProfile(BaseModel):
    """Cross-channel style aggregate for one user; contains no message text."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="communication_profile",
    )
    language = models.CharField(max_length=12, default="fr")
    register = models.CharField(max_length=20, default="neutral")
    address_form = models.CharField(max_length=12, default="vous")
    verbosity = models.CharField(max_length=12, default="balanced")
    warmth = models.CharField(max_length=12, default="warm")
    emoji_level = models.CharField(max_length=12, default="none")
    technical_level = models.CharField(max_length=12, default="simple")
    sample_count = models.PositiveIntegerField(default=0)
    confidence = models.JSONField(default=dict, blank=True)
    signals = models.JSONField(default=dict, blank=True)


class ConversationSummary(BaseModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="summaries"
    )
    source_first_message = models.ForeignKey(
        ConversationMessage,
        on_delete=models.CASCADE,
        related_name="summaries_started",
    )
    source_last_message = models.ForeignKey(
        ConversationMessage,
        on_delete=models.CASCADE,
        related_name="summaries_ended",
    )
    source_message_ids = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    prompt_version = models.CharField(max_length=40)
    content_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default="VALIDATED")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "content_hash"],
                name="conv_summary_content_hash_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["conversation", "created_at"], name="conv_summary_time_idx"
            )
        ]


class ConversationEpisode(BaseModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="episodes"
    )
    topic = models.CharField(max_length=40)
    outcome = models.CharField(max_length=40)
    summary = models.CharField(max_length=500)
    source_message_ids = models.JSONField(default=list)
    tool_references = models.JSONField(default=list)
    status = models.CharField(max_length=20, default="CLOSED")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["conversation", "topic", "created_at"],
                name="conv_episode_topic_idx",
            )
        ]


class MemoryFact(BaseModel):
    """Governed social preference, never an authorization or business record."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memory_facts"
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        related_name="memory_facts",
        blank=True,
        null=True,
    )
    fact_type = models.CharField(max_length=40)
    value = models.JSONField()
    provenance = models.JSONField(default=dict)
    confidence = models.DecimalField(max_digits=4, decimal_places=3)
    sensitivity = models.CharField(max_length=20, default="LOW")
    consent = models.CharField(max_length=20, default="EXPLICIT")
    valid_from = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(blank=True, null=True)
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, blank=True, null=True
    )
    deleted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "fact_type", "deleted_at"],
                name="conv_memory_fact_user_idx",
            )
        ]
