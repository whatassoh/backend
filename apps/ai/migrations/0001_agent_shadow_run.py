import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("conversations", "0002_add_template_message_type")]

    operations = [
        migrations.CreateModel(
            name="AgentShadowRun",
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
                ("graph_version", models.CharField(max_length=40)),
                ("status", models.CharField(max_length=30)),
                ("route", models.CharField(blank=True, max_length=50)),
                ("planned_tools", models.JSONField(default=list)),
                ("risk_level", models.CharField(blank=True, max_length=4)),
                ("parity", models.BooleanField(blank=True, null=True)),
                ("comparison", models.JSONField(default=dict)),
                ("state_snapshot", models.JSONField(default=dict)),
                ("latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "error_category",
                    models.CharField(blank=True, max_length=80, null=True),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_shadow_runs",
                        to="conversations.conversation",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="agentshadowrun",
            index=models.Index(
                fields=["status", "created_at"], name="ai_shadow_status_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="agentshadowrun",
            index=models.Index(
                fields=["conversation", "created_at"], name="ai_shadow_conv_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="agentshadowrun",
            constraint=models.UniqueConstraint(
                fields=("conversation", "correlation_id"),
                name="ai_shadow_conv_correlation_unique",
            ),
        ),
    ]
