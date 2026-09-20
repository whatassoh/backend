# config/settings/base.py

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
    "apps.stores",
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
    "django.middleware.security.SecurityMiddleware",
    "common.middleware.CorrelationIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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

# Store-offer notifications are delivery concerns, distinct from StoreOffer.
STORE_OFFER_NOTIFICATION_MAX_ATTEMPTS = env.int("STORE_OFFER_NOTIFICATION_MAX_ATTEMPTS", default=3)
STORE_OFFER_NOTIFICATION_RETRY_BASE_SECONDS = env.int("STORE_OFFER_NOTIFICATION_RETRY_BASE_SECONDS", default=30)
STORE_OFFER_NOTIFICATION_RETRY_MAX_SECONDS = env.int("STORE_OFFER_NOTIFICATION_RETRY_MAX_SECONDS", default=900)
STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS = env.int("STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS", default=900)
STORE_MATCHING_RECONCILIATION_BATCH_SIZE = env.int("STORE_MATCHING_RECONCILIATION_BATCH_SIZE", default=100)


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
