"""Central, provider-agnostic failure decisions for asynchronous work."""

from dataclasses import dataclass
from random import SystemRandom

from django.conf import settings
from django.db import IntegrityError, OperationalError
from redis.exceptions import RedisError

from apps.messaging.domain.exceptions import (
    MessagingAuthenticationError,
    MessagingConfigurationError,
    MessagingError,
    MessagingPermanentError,
    MessagingRateLimitError,
    MessagingTemporaryError,
)
from apps.telegram.exceptions import (
    TelegramAPIError,
    TelegramAuthenticationError,
    TelegramConfigurationError,
    TelegramInvalidPayloadError,
    TelegramRateLimitError,
)
from apps.whatsapp.domain.exceptions import (
    WhatsAppTemplateConfigurationError,
    WhatsAppTemplateValidationError,
)
from apps.whatsapp.exceptions import (
    WhatsAppAPIError,
    WhatsAppAuthenticationError,
    WhatsAppConfigurationError,
    WhatsAppInvalidPayloadError,
    WhatsAppRateLimitError,
)


@dataclass(frozen=True)
class FailureDecision:
    category: str
    retryable: bool
    retry_after: int | None = None


class FailureClassifier:
    """Maps adapters into a small operational taxonomy; never domain outcomes."""

    @staticmethod
    def classify(exc: Exception) -> FailureDecision:
        if isinstance(exc, RedisError):
            return FailureDecision("TRANSIENT_PROVIDER", True)
        if isinstance(
            exc,
            (
                WhatsAppTemplateConfigurationError,
                WhatsAppConfigurationError,
                TelegramConfigurationError,
            ),
        ):
            return FailureDecision("CONFIGURATION", False)
        if isinstance(
            exc,
            (
                WhatsAppTemplateValidationError,
                WhatsAppInvalidPayloadError,
                TelegramInvalidPayloadError,
            ),
        ):
            return FailureDecision("PERMANENT_PAYLOAD", False)
        if isinstance(exc, (WhatsAppAuthenticationError, TelegramAuthenticationError)):
            return FailureDecision("CONFIGURATION", False)
        if isinstance(exc, WhatsAppRateLimitError):
            return FailureDecision(
                "RATE_LIMIT", True, getattr(exc, "retry_after", None)
            )
        if isinstance(exc, TelegramRateLimitError):
            return FailureDecision("RATE_LIMIT", True, exc.retry_after)
        if isinstance(exc, WhatsAppAPIError):
            return _classify_whatsapp_api_error(exc)
        if isinstance(exc, TelegramAPIError):
            return FailureDecision(
                "TRANSIENT_PROVIDER" if exc.retryable else "PERMANENT_PROVIDER",
                exc.retryable,
                getattr(exc, "retry_after", None),
            )
        if isinstance(exc, MessagingRateLimitError):
            return FailureDecision("RATE_LIMIT", True, exc.retry_after)
        if isinstance(exc, MessagingTemporaryError):
            return FailureDecision("TRANSIENT_PROVIDER", True)
        if isinstance(exc, MessagingAuthenticationError):
            return FailureDecision("CONFIGURATION", False)
        if isinstance(exc, MessagingConfigurationError):
            return FailureDecision("CONFIGURATION", False)
        if isinstance(exc, MessagingPermanentError):
            return FailureDecision("PERMANENT_PROVIDER", False)
        if isinstance(exc, IntegrityError):
            return FailureDecision("PERMANENT_PROVIDER", False)
        # Database operational failures are intentionally not retried blindly:
        # a caller must translate a known deadlock/serialization failure first.
        if isinstance(exc, OperationalError):
            return FailureDecision("UNKNOWN", False)
        if isinstance(exc, MessagingError):
            return FailureDecision(
                "TRANSIENT_PROVIDER" if exc.retryable else "PERMANENT_PROVIDER",
                exc.retryable,
            )
        return FailureDecision("UNKNOWN", False)


def _classify_whatsapp_api_error(exc: WhatsAppAPIError) -> FailureDecision:
    """Classify Meta failures from their HTTP and structured error details."""
    status_code = exc.status_code
    response_error = (exc.response_data or {}).get("error")
    response_error = response_error if isinstance(response_error, dict) else {}
    meta_code = response_error.get("code")

    if status_code == 429:
        return FailureDecision("RATE_LIMIT", True, getattr(exc, "retry_after", None))
    if status_code in {401, 403}:
        return FailureDecision("CONFIGURATION", False)
    if status_code is not None and 500 <= status_code <= 599:
        return FailureDecision("TRANSIENT_PROVIDER", True)
    # Meta uses these codes for malformed or invalid message parameters.
    if meta_code in {100, 131000, 131008, 131009}:
        return FailureDecision("PERMANENT_PAYLOAD", False)
    if status_code in {400, 404, 405, 410, 422}:
        return FailureDecision("PERMANENT_PROVIDER", False)
    return FailureDecision(
        "TRANSIENT_PROVIDER" if exc.retryable else "PERMANENT_PROVIDER",
        exc.retryable,
        getattr(exc, "retry_after", None),
    )


class NotificationRetryPolicy:
    """Bounded exponential retry policy shared by notification delivery tasks."""

    def __init__(self, *, randomizer=None):
        self.randomizer = randomizer or SystemRandom()

    @property
    def max_attempts(self):
        return settings.NOTIFICATION_DELIVERY_MAX_ATTEMPTS

    def countdown(self, attempt_number: int, *, retry_after: int | None = None) -> int:
        maximum = settings.NOTIFICATION_DELIVERY_RETRY_MAX_SECONDS
        if retry_after is not None:
            return min(max(0, retry_after), maximum)
        delay = min(
            settings.NOTIFICATION_DELIVERY_RETRY_BASE_SECONDS
            * 2 ** (attempt_number - 1),
            maximum,
        )
        if settings.NOTIFICATION_DELIVERY_RETRY_JITTER:
            return self.randomizer.randint(max(1, delay // 2), delay)
        return delay
