import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0002_add_template_message_type"),
        ("messaging", "0010_order_customer_terminal_notification_kinds"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationInboxItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveBigIntegerField()),
                ("payload", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("PROCESSING", "Processing"),
                            ("PROCESSED", "Processed"),
                            ("FAILED_RETRYABLE", "Failed retryable"),
                            ("DEAD_LETTER", "Dead letter"),
                        ],
                        default="PENDING",
                        max_length=30,
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "available_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("fencing_token", models.UUIDField(blank=True, null=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_error_category",
                    models.CharField(blank=True, max_length=80, null=True),
                ),
                (
                    "last_error_message",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbox_items",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "message_event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inbox_item",
                        to="messaging.messageevent",
                    ),
                ),
            ],
            options={"ordering": ["conversation_id", "sequence"]},
        ),
        migrations.CreateModel(
            name="ConversationOutboxItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveBigIntegerField()),
                ("payload", models.JSONField(default=dict)),
                ("idempotency_key", models.CharField(max_length=180, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("SENDING", "Sending"),
                            ("DELIVERED", "Delivered"),
                            ("FAILED_RETRYABLE", "Failed retryable"),
                            ("DEAD_LETTER", "Dead letter"),
                        ],
                        default="PENDING",
                        max_length=30,
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "available_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("fencing_token", models.UUIDField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_error_category",
                    models.CharField(blank=True, max_length=80, null=True),
                ),
                (
                    "last_error_message",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbox_items",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "inbox_item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outbox_item",
                        to="messaging.conversationinboxitem",
                    ),
                ),
            ],
            options={"ordering": ["conversation_id", "sequence"]},
        ),
        migrations.AddConstraint(
            model_name="conversationinboxitem",
            constraint=models.UniqueConstraint(
                fields=("conversation", "sequence"),
                name="msg_inbox_conv_sequence_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="conversationoutboxitem",
            constraint=models.UniqueConstraint(
                fields=("conversation", "sequence"),
                name="msg_outbox_conv_sequence_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationinboxitem",
            index=models.Index(
                fields=["status", "available_at"], name="msg_inbox_ready_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="conversationinboxitem",
            index=models.Index(
                fields=["conversation", "sequence", "status"],
                name="msg_inbox_order_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationoutboxitem",
            index=models.Index(
                fields=["status", "available_at"], name="msg_outbox_ready_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="conversationoutboxitem",
            index=models.Index(
                fields=["conversation", "sequence", "status"],
                name="msg_outbox_order_idx",
            ),
        ),
    ]
