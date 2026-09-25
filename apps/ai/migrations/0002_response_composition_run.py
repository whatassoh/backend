import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0001_agent_shadow_run"),
        ("conversations", "0003_conversation_memory"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResponseCompositionRun",
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
                (
                    "correlation_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("baseline_text", models.TextField()),
                ("candidate_text", models.TextField()),
                ("baseline_hash", models.CharField(max_length=64)),
                ("candidate_hash", models.CharField(max_length=64)),
                ("style", models.JSONField(default=dict)),
                ("accepted", models.BooleanField(default=False)),
                ("reason", models.CharField(max_length=80)),
                ("shadow", models.BooleanField(default=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="composition_runs",
                        to="conversations.conversation",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="responsecompositionrun",
            constraint=models.UniqueConstraint(
                fields=("conversation", "correlation_id"),
                name="ai_composition_conv_correlation_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="responsecompositionrun",
            index=models.Index(
                fields=["accepted", "created_at"], name="ai_composition_accept_idx"
            ),
        ),
    ]
