import hashlib
import uuid

import django.contrib.postgres.indexes
import django.contrib.postgres.search
import django.db.models.deletion
from django.db import migrations, models


def populate_existing_knowledge(apps, schema_editor):
    Source = apps.get_model("knowledge", "KnowledgeSource")
    Chunk = apps.get_model("knowledge", "KnowledgeChunk")
    for source in Source.objects.iterator(chunk_size=200):
        source.content_hash = hashlib.sha256(source.content.encode()).hexdigest()
        source.save(update_fields=["content_hash"])
    for chunk in Chunk.objects.iterator(chunk_size=500):
        chunk.content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()
        chunk.token_count = max(1, (len(chunk.content) + 3) // 4)
        chunk.save(update_fields=["content_hash", "token_count"])
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE knowledge_knowledgechunk AS chunk
                SET search_vector =
                    setweight(
                        to_tsvector(
                            CASE
                                WHEN source.language LIKE 'fr%%'
                                THEN 'french'::regconfig
                                WHEN source.language LIKE 'en%%'
                                THEN 'english'::regconfig
                                ELSE 'simple'::regconfig
                            END,
                            coalesce(source.title, '')
                        ),
                        'A'
                    ) ||
                    setweight(
                        to_tsvector(
                            CASE
                                WHEN source.language LIKE 'fr%%'
                                THEN 'french'::regconfig
                                WHEN source.language LIKE 'en%%'
                                THEN 'english'::regconfig
                                ELSE 'simple'::regconfig
                            END,
                            coalesce(chunk.content, '')
                        ),
                        'B'
                    )
                FROM knowledge_knowledgesource AS source
                WHERE chunk.source_id = source.id
                """
            )


def create_vector_storage(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cursor.execute(
            """
            ALTER TABLE knowledge_knowledgechunk
            ADD COLUMN IF NOT EXISTS embedding vector(1024)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS knowledge_chunk_embedding_hnsw
            ON knowledge_knowledgechunk
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )


def drop_vector_storage(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS knowledge_chunk_embedding_hnsw")
        cursor.execute(
            "ALTER TABLE knowledge_knowledgechunk DROP COLUMN IF EXISTS embedding"
        )


class Migration(migrations.Migration):
    dependencies = [("knowledge", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="knowledgesource",
            name="namespace",
            field=models.SlugField(default="customer-help", max_length=80),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="content_hash",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="canonical_url",
            field=models.URLField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="audience",
            field=models.CharField(
                choices=[
                    ("PUBLIC", "Public"),
                    ("AUTHENTICATED", "Authenticated users"),
                    ("RESTRICTED", "Explicit grants"),
                    ("INTERNAL", "Internal staff"),
                ],
                default="PUBLIC",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="trust_level",
            field=models.CharField(
                choices=[
                    ("FIRST_PARTY", "First-party"),
                    ("REVIEWED", "Human reviewed"),
                    ("EXTERNAL", "External and untrusted"),
                ],
                default="REVIEWED",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="sensitivity",
            field=models.CharField(
                choices=[
                    ("PUBLIC", "Public"),
                    ("INTERNAL", "Internal"),
                    ("CONFIDENTIAL", "Confidential"),
                ],
                default="PUBLIC",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="valid_from",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="valid_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="knowledgesource",
            name="security_report",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="knowledgesource",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Draft"),
                    ("ACTIVE", "Active"),
                    ("INACTIVE", "Inactive"),
                    ("QUARANTINED", "Quarantined"),
                    ("REJECTED", "Rejected"),
                ],
                default="ACTIVE",
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="knowledgesource",
            name="knowledge_source_key_version_unique",
        ),
        migrations.RemoveIndex(
            model_name="knowledgesource", name="knowledge_src_key_status_idx"
        ),
        migrations.RemoveIndex(
            model_name="knowledgesource", name="knowledge_src_status_lang_idx"
        ),
        migrations.AddConstraint(
            model_name="knowledgesource",
            constraint=models.UniqueConstraint(
                fields=("namespace", "key", "version"),
                name="knowledge_source_ns_key_ver_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgesource",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "ACTIVE")),
                fields=("namespace", "key"),
                name="knowledge_source_one_active_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgesource",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("valid_from__isnull", True))
                    | models.Q(("valid_until__isnull", True))
                    | models.Q(("valid_until__gt", models.F("valid_from")))
                ),
                name="knowledge_source_valid_window_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgesource",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("sensitivity", "PUBLIC"))
                    | models.Q(("audience__in", ["RESTRICTED", "INTERNAL"]))
                ),
                name="knowledge_source_sensitive_audience_check",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgesource",
            index=models.Index(
                fields=["namespace", "key", "status"],
                name="knowledge_src_ns_key_stat_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgesource",
            index=models.Index(
                fields=["namespace", "status", "language", "audience"],
                name="knowledge_src_scope_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgesource",
            index=models.Index(fields=["valid_until"], name="knowledge_src_expiry_idx"),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="content_hash",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="section_path",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="token_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(
                blank=True, editable=False, null=True
            ),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="injection_risk",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=4),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="embedding_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("INDEXING", "Indexing"),
                    ("READY", "Ready"),
                    ("FAILED", "Failed"),
                    ("SKIPPED", "Skipped"),
                ],
                default="PENDING",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="embedding_model",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="embedding_content_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="embedding_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="knowledgechunk",
            name="embedding_error",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=models.Index(
                fields=["embedding_status", "updated_at"],
                name="knowledge_chunk_embed_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="knowledge_chunk_fts_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("injection_risk__gte", 0))
                    & models.Q(("injection_risk__lte", 1))
                ),
                name="knowledge_chunk_risk_range_check",
            ),
        ),
        migrations.CreateModel(
            name="KnowledgeAccessGrant",
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
                    "principal_type",
                    models.CharField(
                        choices=[
                            ("USER", "User"),
                            ("ROLE", "Role"),
                            ("STORE", "Store"),
                        ],
                        max_length=12,
                    ),
                ),
                ("principal_id", models.CharField(max_length=64)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_grants",
                        to="knowledge.knowledgesource",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="knowledgeaccessgrant",
            constraint=models.UniqueConstraint(
                fields=("source", "principal_type", "principal_id"),
                name="knowledge_grant_principal_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgeaccessgrant",
            index=models.Index(
                fields=["principal_type", "principal_id"],
                name="knowledge_grant_lookup_idx",
            ),
        ),
        migrations.CreateModel(
            name="KnowledgeRetrievalAudit",
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
                ("query_hash", models.CharField(max_length=64)),
                ("acl_fingerprint", models.CharField(max_length=64)),
                ("namespace", models.SlugField(max_length=80)),
                ("language", models.CharField(max_length=10)),
                ("strategy", models.CharField(max_length=30)),
                ("chunk_ids", models.JSONField(default=list)),
                ("source_ids", models.JSONField(default=list)),
                ("candidate_count", models.PositiveSmallIntegerField(default=0)),
                ("cache_hit", models.BooleanField(default=False)),
                ("grounded", models.BooleanField(blank=True, null=True)),
                (
                    "no_answer_reason",
                    models.CharField(blank=True, default="", max_length=40),
                ),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                (
                    "correlation_id",
                    models.CharField(blank=True, max_length=128, null=True),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="knowledgeretrievalaudit",
            index=models.Index(
                fields=["created_at"], name="knowledge_audit_created_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgeretrievalaudit",
            index=models.Index(
                fields=["namespace", "strategy", "created_at"],
                name="knowledge_audit_strategy_idx",
            ),
        ),
        migrations.RunPython(populate_existing_knowledge, migrations.RunPython.noop),
        migrations.RunPython(create_vector_storage, drop_vector_storage),
    ]
