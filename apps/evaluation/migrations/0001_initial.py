import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

TASK = [
    ("MESSAGE_UNDERSTANDING", "Message understanding"),
    ("CATALOG_RESOLUTION", "Catalog resolution"),
    ("CLARIFICATION", "Clarification"),
    ("KNOWLEDGE_RETRIEVAL", "Knowledge retrieval"),
]


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="DatasetVersion",
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
                ("name", models.CharField(max_length=100)),
                ("version", models.PositiveIntegerField()),
                ("task_type", models.CharField(choices=TASK, max_length=30)),
                (
                    "status",
                    models.CharField(
                        choices=[("DRAFT", "Draft"), ("PUBLISHED", "Published")],
                        default="DRAFT",
                        max_length=20,
                    ),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.CreateModel(
            name="DatasetCandidate",
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
                ("task_type", models.CharField(choices=TASK, max_length=30)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("ACCEPTED", "Accepted"),
                            ("REJECTED", "Rejected"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("sanitized_input", models.TextField()),
                ("predicted_output", models.JSONField(default=dict)),
                ("expected_output", models.JSONField(blank=True, null=True)),
                (
                    "label_provenance",
                    models.CharField(blank=True, max_length=40, null=True),
                ),
                ("quality_signals", models.JSONField(blank=True, default=dict)),
                (
                    "source_reference",
                    models.CharField(blank=True, max_length=128, null=True),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "reviewer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_dataset_candidates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="DatasetExample",
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
                ("task_type", models.CharField(choices=TASK, max_length=30)),
                ("input", models.TextField()),
                ("expected_output", models.JSONField()),
                ("group_key", models.CharField(max_length=64)),
                (
                    "source_candidate",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="example",
                        to="evaluation.datasetcandidate",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="DatasetVersionExample",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "split",
                    models.CharField(
                        choices=[
                            ("TRAIN", "Train"),
                            ("VALIDATION", "Validation"),
                            ("TEST", "Test"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "dataset_example",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="evaluation.datasetexample",
                    ),
                ),
                (
                    "dataset_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="evaluation.datasetversion",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="datasetversion",
            name="examples",
            field=models.ManyToManyField(
                related_name="versions",
                through="evaluation.DatasetVersionExample",
                to="evaluation.datasetexample",
            ),
        ),
        migrations.CreateModel(
            name="EvaluationRun",
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
                ("task_type", models.CharField(choices=TASK, max_length=30)),
                ("provider", models.CharField(max_length=50)),
                ("model", models.CharField(blank=True, max_length=100)),
                ("prompt_version", models.CharField(max_length=100)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("RUNNING", "Running"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                        ],
                        default="RUNNING",
                        max_length=20,
                    ),
                ),
                ("metrics", models.JSONField(blank=True, default=dict)),
                (
                    "dataset_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="evaluation.datasetversion",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="EvaluationResult",
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
                ("predicted_output", models.JSONField(default=dict)),
                ("passed", models.BooleanField()),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "dataset_example",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="evaluation.datasetexample",
                    ),
                ),
                (
                    "evaluation_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="results",
                        to="evaluation.evaluationrun",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="datasetversionexample",
            constraint=models.UniqueConstraint(
                fields=("dataset_version", "dataset_example"),
                name="evaluation_version_example_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="datasetversion",
            constraint=models.UniqueConstraint(
                fields=("name", "version"), name="evaluation_dataset_version_unique"
            ),
        ),
    ]
