import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0003_response_composition_policy"),
        ("conversations", "0004_user_communication_profile"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentExecutionRun",
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
                ("executed_tools", models.JSONField(default=list)),
                ("result_statuses", models.JSONField(default=dict)),
                ("risk_level", models.CharField(blank=True, max_length=4)),
                ("policy_decision", models.CharField(blank=True, max_length=40)),
                ("latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "error_category",
                    models.CharField(blank=True, max_length=80, null=True),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_execution_runs",
                        to="conversations.conversation",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="agentexecutionrun",
            index=models.Index(
                fields=["status", "created_at"], name="ai_execution_status_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="agentexecutionrun",
            index=models.Index(
                fields=["conversation", "created_at"], name="ai_execution_conv_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="agentexecutionrun",
            constraint=models.UniqueConstraint(
                fields=("conversation", "correlation_id"),
                name="ai_execution_conv_correlation_unique",
            ),
        ),
    ]
