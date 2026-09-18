import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="KnowledgeSource",
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
                ("key", models.SlugField(max_length=100)),
                ("title", models.CharField(max_length=255)),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            ("FAQ", "FAQ"),
                            ("POLICY", "Policy"),
                            ("GUIDE", "Guide"),
                            ("OPERATIONS", "Operations"),
                            ("OTHER", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("ACTIVE", "Active"), ("INACTIVE", "Inactive")],
                        default="ACTIVE",
                        max_length=20,
                    ),
                ),
                ("language", models.CharField(default="fr", max_length=10)),
                ("version", models.PositiveIntegerField(default=1)),
                ("content", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={"ordering": ["key", "-version"]},
        ),
        migrations.CreateModel(
            name="KnowledgeChunk",
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
                ("chunk_index", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="knowledge.knowledgesource",
                    ),
                ),
            ],
            options={"ordering": ["source_id", "chunk_index"]},
        ),
        migrations.AddConstraint(
            model_name="knowledgesource",
            constraint=models.UniqueConstraint(
                fields=("key", "version"), name="knowledge_source_key_version_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.UniqueConstraint(
                fields=("source", "chunk_index"),
                name="knowledge_chunk_source_index_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgesource",
            index=models.Index(
                fields=["key", "status"], name="knowledge_src_key_status_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgesource",
            index=models.Index(
                fields=["status", "language"], name="knowledge_src_status_lang_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=models.Index(
                fields=["source", "chunk_index"], name="knowledge_chunk_source_idx"
            ),
        ),
    ]
