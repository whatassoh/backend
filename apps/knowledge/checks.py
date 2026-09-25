from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def knowledge_configuration_checks(app_configs, **kwargs):
    errors = []
    if not 0 <= settings.AI_RAG_ROLLOUT_PERCENT <= 100:
        errors.append(
            Error("RAG rollout must be between 0 and 100.", id="knowledge.E010")
        )
    dimensions = settings.AI_RAG_EMBEDDING_DIMENSIONS
    if dimensions != 1024:
        errors.append(
            Error(
                "AI_RAG_EMBEDDING_DIMENSIONS must match vector(1024).",
                id="knowledge.E001",
            )
        )
    target = settings.AI_RAG_CHUNK_TARGET_CHARACTERS
    maximum = settings.AI_RAG_CHUNK_MAX_CHARACTERS
    overlap = settings.AI_RAG_CHUNK_OVERLAP_CHARACTERS
    if not 200 <= target <= maximum or not 0 <= overlap < target:
        errors.append(Error("Invalid RAG chunk bounds.", id="knowledge.E002"))
    for name in (
        "AI_RAG_MIN_LEXICAL_SCORE",
        "AI_RAG_MIN_TRIGRAM_SCORE",
        "AI_RAG_MIN_SEMANTIC_SCORE",
        "AI_RAG_MAX_CHUNK_INJECTION_RISK",
    ):
        if not 0 <= getattr(settings, name) <= 1:
            errors.append(
                Error(f"{name} must be between 0 and 1.", id="knowledge.E003")
            )
    if settings.AI_RAG_LEXICAL_WEIGHT < 0 or settings.AI_RAG_SEMANTIC_WEIGHT < 0:
        errors.append(Error("RAG weights cannot be negative.", id="knowledge.E004"))
    if settings.AI_RAG_LEXICAL_WEIGHT + settings.AI_RAG_SEMANTIC_WEIGHT <= 0:
        errors.append(
            Error("At least one RAG weight must be positive.", id="knowledge.E005")
        )
    if settings.AI_RAG_MAX_CONTEXT_CHARACTERS < settings.AI_RAG_CHUNK_MAX_CHARACTERS:
        errors.append(
            Error(
                "RAG context must fit at least one complete chunk.",
                id="knowledge.E009",
            )
        )
    if settings.AI_RAG_SEMANTIC_ENABLED and not settings.AI_RAG_EMBEDDING_MODEL:
        errors.append(
            Error("Semantic RAG requires an embedding model.", id="knowledge.E006")
        )
    if (
        settings.AI_RAG_SEMANTIC_ENABLED
        and settings.AI_RAG_EMBEDDING_PROVIDER == "openai"
        and not settings.OPENAI_API_KEY
    ):
        errors.append(
            Error("OpenAI embeddings require OPENAI_API_KEY.", id="knowledge.E015")
        )
    if (
        settings.AI_RAG_SEMANTIC_ENABLED
        and settings.AI_RAG_EMBEDDING_PROVIDER == "openai"
        and not settings.AI_RAG_EMBEDDING_SEND_DIMENSIONS
    ):
        errors.append(
            Error(
                "OpenAI embeddings must request the configured dimensions.",
                id="knowledge.E016",
            )
        )
    if settings.AI_RAG_EMBEDDING_PROVIDER not in {"openai", "openai_compatible"}:
        errors.append(Error("Unknown RAG embedding provider.", id="knowledge.E007"))
    if (
        settings.AI_RAG_SEMANTIC_ENABLED
        and settings.AI_RAG_EMBEDDING_PROVIDER == "openai_compatible"
        and not settings.AI_RAG_EMBEDDING_BASE_URL.startswith(("http://", "https://"))
    ):
        errors.append(
            Error("Local embeddings require an HTTP(S) base URL.", id="knowledge.E008")
        )
    if settings.AI_RAG_ENABLED and not settings.AI_ENABLED:
        errors.append(
            Warning(
                "AI_RAG_ENABLED has no effect while AI_ENABLED is false.",
                id="knowledge.W001",
            )
        )
    if settings.AI_RAG_SHADOW_ENABLED and not settings.AI_ENABLED:
        errors.append(
            Warning(
                "AI_RAG_SHADOW_ENABLED has no effect while AI_ENABLED is false.",
                id="knowledge.W003",
            )
        )
    if settings.AI_RAG_SEMANTIC_ENABLED and not settings.AI_ENABLED:
        errors.append(
            Warning(
                "AI_RAG_SEMANTIC_ENABLED has no effect while AI_ENABLED is false.",
                id="knowledge.W004",
            )
        )
    if settings.AI_RAG_ENABLED and settings.AI_RAG_ROLLOUT_PERCENT == 0:
        errors.append(
            Warning(
                "AI_RAG_ENABLED is set but its rollout percentage is zero.",
                id="knowledge.W002",
            )
        )
    positive_integer_settings = (
        "AI_RAG_MAX_RESULTS",
        "AI_RAG_CANDIDATE_LIMIT",
        "AI_RAG_MAX_CHUNKS_PER_SOURCE",
        "AI_RAG_MAX_QUERY_CHARACTERS",
        "AI_RAG_MAX_SOURCE_CHARACTERS",
        "AI_RAG_MAX_METADATA_CHARACTERS",
        "AI_RAG_MAX_CONTEXT_CHARACTERS",
        "AI_RAG_RRF_RANK_CONSTANT",
        "AI_RAG_EMBEDDING_BATCH_SIZE",
        "AI_RAG_EMBEDDING_MAX_ATTEMPTS",
        "AI_RAG_EMBEDDING_LEASE_SECONDS",
        "AI_RAG_AUDIT_RETENTION_DAYS",
    )
    for name in positive_integer_settings:
        if getattr(settings, name) <= 0:
            errors.append(
                Error(f"{name} must be strictly positive.", id="knowledge.E011")
            )
    if settings.AI_RAG_CANDIDATE_LIMIT < settings.AI_RAG_MAX_RESULTS:
        errors.append(
            Error(
                "AI_RAG_CANDIDATE_LIMIT must cover AI_RAG_MAX_RESULTS.",
                id="knowledge.E012",
            )
        )
    if settings.AI_RAG_MAX_CHUNKS_PER_SOURCE > settings.AI_RAG_MAX_RESULTS:
        errors.append(
            Error(
                "AI_RAG_MAX_CHUNKS_PER_SOURCE cannot exceed AI_RAG_MAX_RESULTS.",
                id="knowledge.E013",
            )
        )
    if settings.AI_RAG_CACHE_SECONDS < 0:
        errors.append(
            Error(
                "AI_RAG_CACHE_SECONDS cannot be negative.",
                id="knowledge.E014",
            )
        )
    return errors
