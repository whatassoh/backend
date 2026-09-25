import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0002_add_template_message_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationStyleProfile",
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
                ("language", models.CharField(default="fr", max_length=12)),
                ("register", models.CharField(default="neutral", max_length=20)),
                ("address_form", models.CharField(default="vous", max_length=12)),
                ("verbosity", models.CharField(default="balanced", max_length=12)),
                ("warmth", models.CharField(default="warm", max_length=12)),
                ("emoji_level", models.CharField(default="none", max_length=12)),
                ("technical_level", models.CharField(default="simple", max_length=12)),
                ("sample_count", models.PositiveIntegerField(default=0)),
                ("signals", models.JSONField(blank=True, default=dict)),
                (
                    "conversation",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="style_profile",
                        to="conversations.conversation",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ConversationSummary",
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
                ("source_message_ids", models.JSONField(default=list)),
                ("summary", models.JSONField(default=dict)),
                ("prompt_version", models.CharField(max_length=40)),
                ("content_hash", models.CharField(max_length=64)),
                ("status", models.CharField(default="VALIDATED", max_length=20)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="summaries",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "source_first_message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="summaries_started",
                        to="conversations.conversationmessage",
                    ),
                ),
                (
                    "source_last_message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="summaries_ended",
                        to="conversations.conversationmessage",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ConversationEpisode",
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
                ("topic", models.CharField(max_length=40)),
                ("outcome", models.CharField(max_length=40)),
                ("summary", models.CharField(max_length=500)),
                ("source_message_ids", models.JSONField(default=list)),
                ("tool_references", models.JSONField(default=list)),
                ("status", models.CharField(default="CLOSED", max_length=20)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="episodes",
                        to="conversations.conversation",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="MemoryFact",
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
                ("fact_type", models.CharField(max_length=40)),
                ("value", models.JSONField()),
                ("provenance", models.JSONField(default=dict)),
                ("confidence", models.DecimalField(decimal_places=3, max_digits=4)),
                ("sensitivity", models.CharField(default="LOW", max_length=20)),
                ("consent", models.CharField(default="EXPLICIT", max_length=20)),
                ("valid_from", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="memory_facts",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "superseded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="conversations.memoryfact",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memory_facts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="conversationsummary",
            constraint=models.UniqueConstraint(
                fields=("conversation", "content_hash"),
                name="conv_summary_content_hash_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationsummary",
            index=models.Index(
                fields=["conversation", "created_at"], name="conv_summary_time_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="conversationepisode",
            index=models.Index(
                fields=["conversation", "topic", "created_at"],
                name="conv_episode_topic_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="memoryfact",
            index=models.Index(
                fields=["user", "fact_type", "deleted_at"],
                name="conv_memory_fact_user_idx",
            ),
        ),
    ]
