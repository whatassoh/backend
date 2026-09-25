# config/settings/base.py

from datetime import timedelta
from pathlib import Path

import environ
import structlog

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    WHATSAPP_ENABLED=(bool, False),
    WHATSAPP_REQUEST_TIMEOUT=(float, 10.0),
    TELEGRAM_ENABLED=(bool, False),
    TELEGRAM_REQUEST_TIMEOUT=(float, 10.0),
    SELLER_DASHBOARD_LINK_TTL_SECONDS=(int, 600),
    # Provisional MVP default. Deployments must configure this explicitly before
    # driver matching is enabled; lot 9.3 only supplies the location ingestion.
    DRIVER_LOCATION_FRESHNESS_SECONDS=(int, 900),
    DRIVER_MATCHING_RADIUS_METERS=(int, 5000),
    DRIVER_MATCHING_MAX_CANDIDATES=(int, 10),
    DRIVER_OFFER_TTL_SECONDS=(int, 90),
)

environ.Env.read_env(BASE_DIR / ".env")


# ---------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env.bool("DJANGO_DEBUG", default=False)

DRIVER_LOCATION_FRESHNESS_SECONDS = env.int(
    "DRIVER_LOCATION_FRESHNESS_SECONDS", default=900
)
DRIVER_MATCHING_RADIUS_METERS = env.int("DRIVER_MATCHING_RADIUS_METERS", default=5000)
DRIVER_MATCHING_MAX_CANDIDATES = env.int("DRIVER_MATCHING_MAX_CANDIDATES", default=10)
DRIVER_OFFER_TTL_SECONDS = env.int("DRIVER_OFFER_TTL_SECONDS", default=90)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)


# ---------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # GeoDjango
    "django.contrib.gis",
]

THIRD_PARTY_APPS = [
    "django_prometheus",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "django_celery_beat",
    "django.contrib.postgres",
]

LOCAL_APPS = [
    "apps.identity",
    "apps.stores.apps.StoresConfig",
    "apps.drivers",
    "apps.catalog",
    "apps.orders",
    "apps.audit",
    "apps.messaging",
    "apps.conversations",
    "apps.whatsapp",
    "apps.telegram",
    "apps.ai",
    "apps.knowledge",
    "apps.evaluation",
]

INSTALLED_APPS = [
    *DJANGO_APPS,
    *THIRD_PARTY_APPS,
    *LOCAL_APPS,
]


# ---------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "common.middleware.CorrelationIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]


ROOT_URLCONF = "config.urls"


# ---------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ---------------------------------------------------------------------
# Database - PostgreSQL / PostGIS
# ---------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
        "CONN_MAX_AGE": 60,
    }
}


# ---------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.MinimumLengthValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.CommonPasswordValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.NumericPasswordValidator"),
    },
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_USER_MODEL = "identity.User"


# ---------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------

LANGUAGE_CODE = "fr-fr"

TIME_ZONE = "Africa/Douala"

USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------
# Redis cache
# ---------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
        },
    }
}


# ---------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        ("rest_framework_simplejwt.authentication.JWTAuthentication"),
        ("rest_framework.authentication.SessionAuthentication"),
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": (
        "common.pagination.page_number.StandardPageNumberPagination"
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
    },
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": ("drf_spectacular.openapi.AutoSchema"),
    "EXCEPTION_HANDLER": ("common.exceptions.handler.api_exception_handler"),
}


# ---------------------------------------------------------------------
# WhatsApp Cloud API
# ---------------------------------------------------------------------

WHATSAPP_ENABLED = env.bool("WHATSAPP_ENABLED", default=False)
WHATSAPP_ACCESS_TOKEN = env("WHATSAPP_ACCESS_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_BUSINESS_ACCOUNT_ID = env("WHATSAPP_BUSINESS_ACCOUNT_ID", default="")
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN", default="")
WHATSAPP_APP_SECRET = env("WHATSAPP_APP_SECRET", default="")
WHATSAPP_REQUEST_TIMEOUT = env.float("WHATSAPP_REQUEST_TIMEOUT", default=10.0)
META_GRAPH_API_VERSION = env("META_GRAPH_API_VERSION", default="v26.0")
WHATSAPP_TEMPLATE_LANGUAGE_CODE = env("WHATSAPP_TEMPLATE_LANGUAGE_CODE", default="fr")
WHATSAPP_TEMPLATE_ORDER_UPDATE = env("WHATSAPP_TEMPLATE_ORDER_UPDATE", default="")
WHATSAPP_TEMPLATE_ORDER_ACTION_REQUIRED = env(
    "WHATSAPP_TEMPLATE_ORDER_ACTION_REQUIRED",
    default="",
)
WHATSAPP_TEMPLATE_OPERATIONAL_ACTION_REQUIRED = env(
    "WHATSAPP_TEMPLATE_OPERATIONAL_ACTION_REQUIRED",
    default="",
)


# ---------------------------------------------------------------------
# Telegram Bot API
# ---------------------------------------------------------------------

TELEGRAM_ENABLED = env.bool("TELEGRAM_ENABLED", default=False)
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")
TELEGRAM_WEBHOOK_URL = env("TELEGRAM_WEBHOOK_URL", default="")
TELEGRAM_API_BASE_URL = env("TELEGRAM_API_BASE_URL", default="https://api.telegram.org")
TELEGRAM_REQUEST_TIMEOUT = env.float("TELEGRAM_REQUEST_TIMEOUT", default=10.0)


# ---------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------

# Keeping AI disabled by default makes local and test startup independent from
# external credentials.  The mock provider remains available through explicit
# dependency injection for deterministic application tests.
AI_ENABLED = env.bool("AI_ENABLED", default=False)
AI_PROVIDER = env("AI_PROVIDER", default="mock").lower()
AI_MODEL = env("AI_MODEL", default="")
AI_TIMEOUT_SECONDS = env.float("AI_TIMEOUT_SECONDS", default=15.0)
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")

# AI rollout controls are intentionally independent environment switches. New
# agent capabilities remain off until their implementation lot passes its
# regression and security gates. AI_EMERGENCY_STOP is the operational kill
# switch: it blocks model calls while deterministic channel actions stay live.
AI_EMERGENCY_STOP = env.bool("AI_EMERGENCY_STOP", default=False)
AI_AGENT_ENABLED = env.bool("AI_AGENT_ENABLED", default=False)
AI_AGENT_SHADOW_ENABLED = env.bool("AI_AGENT_SHADOW_ENABLED", default=False)
AI_AGENT_TOOLS_ENABLED = env.bool("AI_AGENT_TOOLS_ENABLED", default=False)
AI_AGENT_WRITE_TOOLS_ENABLED = env.bool("AI_AGENT_WRITE_TOOLS_ENABLED", default=False)
AI_AGENT_MEMORY_ENABLED = env.bool("AI_AGENT_MEMORY_ENABLED", default=False)
AI_CONVERSATION_MEMORY_ENABLED = env.bool(
    "AI_CONVERSATION_MEMORY_ENABLED", default=False
)
AI_MEMORY_SUMMARY_ENABLED = env.bool("AI_MEMORY_SUMMARY_ENABLED", default=False)
AI_NATURAL_COMPOSER_ENABLED = env.bool("AI_NATURAL_COMPOSER_ENABLED", default=False)
AI_NATURAL_COMPOSER_SHADOW_ENABLED = env.bool(
    "AI_NATURAL_COMPOSER_SHADOW_ENABLED", default=False
)
AI_STYLE_ADAPTATION_ENABLED = env.bool("AI_STYLE_ADAPTATION_ENABLED", default=False)
AI_MEMORY_SUMMARY_MIN_MESSAGES = env.int("AI_MEMORY_SUMMARY_MIN_MESSAGES", default=8)
AI_MEMORY_SUMMARY_BATCH_SIZE = env.int("AI_MEMORY_SUMMARY_BATCH_SIZE", default=24)
AI_MEMORY_FACT_TTL_DAYS = env.int("AI_MEMORY_FACT_TTL_DAYS", default=365)
AI_MEMORY_RECONCILIATION_BATCH_SIZE = env.int(
    "AI_MEMORY_RECONCILIATION_BATCH_SIZE", default=100
)
AI_RAG_ENABLED = env.bool("AI_RAG_ENABLED", default=False)
AI_RAG_SHADOW_ENABLED = env.bool("AI_RAG_SHADOW_ENABLED", default=False)
AI_RAG_ROLLOUT_PERCENT = env.int("AI_RAG_ROLLOUT_PERCENT", default=0)
AI_RAG_SEMANTIC_ENABLED = env.bool("AI_RAG_SEMANTIC_ENABLED", default=False)
AI_RAG_EMBEDDING_PROVIDER = env("AI_RAG_EMBEDDING_PROVIDER", default="openai")
AI_RAG_EMBEDDING_MODEL = env("AI_RAG_EMBEDDING_MODEL", default="text-embedding-3-small")
AI_RAG_EMBEDDING_DIMENSIONS = env.int("AI_RAG_EMBEDDING_DIMENSIONS", default=1024)
AI_RAG_EMBEDDING_BASE_URL = env("AI_RAG_EMBEDDING_BASE_URL", default="")
AI_RAG_EMBEDDING_API_KEY = env("AI_RAG_EMBEDDING_API_KEY", default="")
AI_RAG_EMBEDDING_SEND_DIMENSIONS = env.bool(
    "AI_RAG_EMBEDDING_SEND_DIMENSIONS", default=True
)
AI_RAG_MAX_QUERY_CHARACTERS = env.int("AI_RAG_MAX_QUERY_CHARACTERS", default=1200)
AI_RAG_MAX_SOURCE_CHARACTERS = env.int("AI_RAG_MAX_SOURCE_CHARACTERS", default=500_000)
AI_RAG_MAX_METADATA_CHARACTERS = env.int(
    "AI_RAG_MAX_METADATA_CHARACTERS", default=20_000
)
AI_RAG_CHUNK_TARGET_CHARACTERS = env.int("AI_RAG_CHUNK_TARGET_CHARACTERS", default=1200)
AI_RAG_CHUNK_MAX_CHARACTERS = env.int("AI_RAG_CHUNK_MAX_CHARACTERS", default=1800)
AI_RAG_CHUNK_OVERLAP_CHARACTERS = env.int(
    "AI_RAG_CHUNK_OVERLAP_CHARACTERS", default=160
)
AI_RAG_MAX_RESULTS = env.int("AI_RAG_MAX_RESULTS", default=6)
AI_RAG_CANDIDATE_LIMIT = env.int("AI_RAG_CANDIDATE_LIMIT", default=24)
AI_RAG_MAX_CONTEXT_CHARACTERS = env.int("AI_RAG_MAX_CONTEXT_CHARACTERS", default=12_000)
AI_RAG_MAX_CHUNKS_PER_SOURCE = env.int("AI_RAG_MAX_CHUNKS_PER_SOURCE", default=2)
AI_RAG_MIN_LEXICAL_SCORE = env.float("AI_RAG_MIN_LEXICAL_SCORE", default=0.02)
AI_RAG_MIN_TRIGRAM_SCORE = env.float("AI_RAG_MIN_TRIGRAM_SCORE", default=0.12)
AI_RAG_MIN_SEMANTIC_SCORE = env.float("AI_RAG_MIN_SEMANTIC_SCORE", default=0.45)
AI_RAG_MAX_CHUNK_INJECTION_RISK = env.float(
    "AI_RAG_MAX_CHUNK_INJECTION_RISK", default=0.45
)
AI_RAG_RRF_RANK_CONSTANT = env.int("AI_RAG_RRF_RANK_CONSTANT", default=60)
AI_RAG_LEXICAL_WEIGHT = env.float("AI_RAG_LEXICAL_WEIGHT", default=0.55)
AI_RAG_SEMANTIC_WEIGHT = env.float("AI_RAG_SEMANTIC_WEIGHT", default=0.45)
AI_RAG_CACHE_SECONDS = env.int("AI_RAG_CACHE_SECONDS", default=300)
AI_RAG_EMBEDDING_BATCH_SIZE = env.int("AI_RAG_EMBEDDING_BATCH_SIZE", default=32)
AI_RAG_EMBEDDING_MAX_ATTEMPTS = env.int("AI_RAG_EMBEDDING_MAX_ATTEMPTS", default=5)
AI_RAG_EMBEDDING_LEASE_SECONDS = env.int("AI_RAG_EMBEDDING_LEASE_SECONDS", default=900)
AI_RAG_AUDIT_ENABLED = env.bool("AI_RAG_AUDIT_ENABLED", default=True)
AI_RAG_AUDIT_RETENTION_DAYS = env.int("AI_RAG_AUDIT_RETENTION_DAYS", default=30)
AI_VOICE_ENABLED = env.bool("AI_VOICE_ENABLED", default=False)
AI_DECISION_OBSERVABILITY_ENABLED = env.bool(
    "AI_DECISION_OBSERVABILITY_ENABLED", default=True
)
AI_AGENT_MAX_PLAN_STEPS = env.int("AI_AGENT_MAX_PLAN_STEPS", default=4)
AI_AGENT_SHADOW_CACHE_SECONDS = env.int("AI_AGENT_SHADOW_CACHE_SECONDS", default=300)
AI_TOOL_MANIFEST_CACHE_SECONDS = env.int("AI_TOOL_MANIFEST_CACHE_SECONDS", default=300)
AI_TOOL_MAX_RESULT_BYTES = env.int("AI_TOOL_MAX_RESULT_BYTES", default=65536)
MESSAGING_CONFIGURATION_CACHE_SECONDS = env.int(
    "MESSAGING_CONFIGURATION_CACHE_SECONDS", default=60
)


# ---------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------

SPECTACULAR_SETTINGS = {
    "TITLE": "Whatassoh API",
    "DESCRIPTION": (
        "API REST de Whatassoh. Les routes `/api/v1/` exigent un JWT sauf les "
        "webhooks WhatsApp et Telegram. Les schemas de requete et de reponse "
        "sont fournis pour chaque ressource; les webhooks recoivent les payloads "
        "bruts de leurs providers respectifs. Les services AI, grounding, RAG, "
        "clarification et orchestration sont internes et ne constituent pas des "
        "endpoints publics."
    ),
    "VERSION": "1.0.0",
    "SERVE_AUTHENTICATION": [],
    "COMPONENT_SPLIT_REQUEST": True,
    "SERVE_INCLUDE_SCHEMA": False,
}


# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[],
)

SELLER_DASHBOARD_LINK_TTL_SECONDS = env.int(
    "SELLER_DASHBOARD_LINK_TTL_SECONDS", default=600
)
SELLER_DASHBOARD_FRONTEND_URL = env(
    "SELLER_DASHBOARD_FRONTEND_URL",
    default="http://localhost:3000/seller-dashboard/access",
)


# ---------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = TIME_ZONE

CELERY_TASK_TRACK_STARTED = True

CELERY_TASK_TIME_LIMIT = 5 * 60
# Persistent Beat schedules live in PostgreSQL; deploy exactly one Beat
# instance.  No business schedule is introduced by this foundation.
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# ``tasks_reconciliation`` is intentionally separate from delivery tasks;
# Celery autodiscovery only imports ``tasks`` by convention.
CELERY_IMPORTS = ("apps.stores.tasks_reconciliation",)

# Shared defaults for tasks that explicitly choose Celery retry.  Tasks do not
# auto-retry business exceptions.
CELERY_TASK_RETRY_BACKOFF = env.int("CELERY_TASK_RETRY_BACKOFF", default=30)
CELERY_TASK_RETRY_BACKOFF_MAX = env.int("CELERY_TASK_RETRY_BACKOFF_MAX", default=900)
CELERY_TASK_RETRY_JITTER = env.bool("CELERY_TASK_RETRY_JITTER", default=True)

NOTIFICATION_DELIVERY_MAX_ATTEMPTS = env.int(
    "NOTIFICATION_DELIVERY_MAX_ATTEMPTS", default=3
)
NOTIFICATION_DELIVERY_RETRY_BASE_SECONDS = env.int(
    "NOTIFICATION_DELIVERY_RETRY_BASE_SECONDS", default=30
)
NOTIFICATION_DELIVERY_RETRY_MAX_SECONDS = env.int(
    "NOTIFICATION_DELIVERY_RETRY_MAX_SECONDS", default=900
)
NOTIFICATION_DELIVERY_RETRY_JITTER = env.bool(
    "NOTIFICATION_DELIVERY_RETRY_JITTER", default=True
)

OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS = env.int(
    "OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS", default=60
)
OFFER_EXPIRY_SWEEP_BATCH_SIZE = env.int("OFFER_EXPIRY_SWEEP_BATCH_SIZE", default=100)
DRIVER_LOCATION_REMINDER_INTERVAL_SECONDS = env.int(
    "DRIVER_LOCATION_REMINDER_INTERVAL_SECONDS", default=900
)
DRIVER_LOCATION_REMINDER_MAX_ATTEMPTS = env.int(
    "DRIVER_LOCATION_REMINDER_MAX_ATTEMPTS", default=3
)
DRIVER_LOCATION_REMINDER_SWEEP_BATCH_SIZE = env.int(
    "DRIVER_LOCATION_REMINDER_SWEEP_BATCH_SIZE", default=100
)
WORKFLOW_RECONCILIATION_BATCH_SIZE = env.int(
    "WORKFLOW_RECONCILIATION_BATCH_SIZE", default=100
)
NOTIFICATION_PROCESSING_LEASE_SECONDS = env.int(
    "NOTIFICATION_PROCESSING_LEASE_SECONDS", default=900
)
CONVERSATION_INBOX_CHANNELS = tuple(
    value.upper() for value in env.list("CONVERSATION_INBOX_CHANNELS", default=[])
)
CONVERSATION_INBOX_LEASE_SECONDS = env.int(
    "CONVERSATION_INBOX_LEASE_SECONDS", default=300
)
CONVERSATION_OUTBOX_LEASE_SECONDS = env.int(
    "CONVERSATION_OUTBOX_LEASE_SECONDS", default=120
)
CONVERSATION_INBOX_MAX_ATTEMPTS = env.int("CONVERSATION_INBOX_MAX_ATTEMPTS", default=3)
CONVERSATION_OUTBOX_MAX_ATTEMPTS = env.int(
    "CONVERSATION_OUTBOX_MAX_ATTEMPTS", default=3
)
CONVERSATION_RETRY_BASE_SECONDS = env.int("CONVERSATION_RETRY_BASE_SECONDS", default=15)
CONVERSATION_RETRY_MAX_SECONDS = env.int("CONVERSATION_RETRY_MAX_SECONDS", default=300)
CONVERSATION_RECONCILIATION_BATCH_SIZE = env.int(
    "CONVERSATION_RECONCILIATION_BATCH_SIZE", default=100
)
CELERY_BEAT_SCHEDULE = {
    "stores-sweep-expired-offers": {
        "task": "stores.sweep_expired_store_offers",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "drivers-sweep-expired-offers": {
        "task": "drivers.sweep_expired_driver_offers",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "drivers-sweep-location-reminders": {
        "task": "drivers.sweep_location_reminders",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "stores-reconcile-matching": {
        "task": "stores.reconcile_store_matching",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "drivers-reconcile-matching": {
        "task": "drivers.reconcile_driver_matching",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "messaging-reconcile-notifications": {
        "task": "messaging.reconcile_operational_notifications",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "messaging-reconcile-conversation-pipeline": {
        "task": "messaging.reconcile_conversation_pipeline",
        "schedule": timedelta(seconds=OFFER_EXPIRY_SWEEP_INTERVAL_SECONDS),
    },
    "conversations-reconcile-memory": {
        "task": "conversations.reconcile_memory",
        "schedule": timedelta(minutes=5),
    },
    "conversations-expire-memory-facts": {
        "task": "conversations.expire_memory_facts",
        "schedule": timedelta(hours=1),
    },
    "knowledge-reconcile-embeddings": {
        "task": "knowledge.reconcile_embeddings",
        "schedule": timedelta(minutes=10),
    },
    "knowledge-expire-retrieval-audits": {
        "task": "knowledge.expire_retrieval_audits",
        "schedule": timedelta(days=1),
    },
}

# Store-offer notifications are delivery concerns, distinct from StoreOffer.
STORE_OFFER_NOTIFICATION_MAX_ATTEMPTS = env.int(
    "STORE_OFFER_NOTIFICATION_MAX_ATTEMPTS", default=3
)
STORE_OFFER_NOTIFICATION_RETRY_BASE_SECONDS = env.int(
    "STORE_OFFER_NOTIFICATION_RETRY_BASE_SECONDS", default=30
)
STORE_OFFER_NOTIFICATION_RETRY_MAX_SECONDS = env.int(
    "STORE_OFFER_NOTIFICATION_RETRY_MAX_SECONDS", default=900
)
STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS = env.int(
    "STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS", default=900
)
STORE_MATCHING_RECONCILIATION_BATCH_SIZE = env.int(
    "STORE_MATCHING_RECONCILIATION_BATCH_SIZE", default=100
)


# ---------------------------------------------------------------------
# Security defaults
# ---------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_HTTPONLY = True

X_FRAME_OPTIONS = "DENY"

SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_REFERRER_POLICY = "same-origin"

# ---------------------------------------------------------------------
# Structlog
# ---------------------------------------------------------------------


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(
            fmt="iso",
        ),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
)


# ---------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------
PROMETHEUS_METRIC_NAMESPACE = "whatassoh"
