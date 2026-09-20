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
            return FailureDecision("RETRYABLE_TECHNICAL", True)
        if isinstance(exc, (WhatsAppTemplateConfigurationError, WhatsAppConfigurationError, TelegramConfigurationError)):
            return FailureDecision("CONFIGURATION", False)
        if isinstance(exc, (WhatsAppTemplateValidationError, WhatsAppInvalidPayloadError, TelegramInvalidPayloadError)):
            return FailureDecision("PERMANENT_TECHNICAL", False)
        if isinstance(exc, (WhatsAppAuthenticationError, TelegramAuthenticationError)):
            return FailureDecision("AUTHENTICATION", False)
        if isinstance(exc, WhatsAppRateLimitError):
            return FailureDecision("RATE_LIMITED", True)
        if isinstance(exc, TelegramRateLimitError):
            return FailureDecision("RATE_LIMITED", True, exc.retry_after)
        if isinstance(exc, (WhatsAppAPIError, TelegramAPIError)):
            return FailureDecision("RETRYABLE_TECHNICAL" if exc.retryable else "PERMANENT_TECHNICAL", exc.retryable, getattr(exc, "retry_after", None))
        if isinstance(exc, MessagingRateLimitError):
            return FailureDecision("RATE_LIMITED", True, exc.retry_after)
        if isinstance(exc, MessagingTemporaryError):
            return FailureDecision("RETRYABLE_TECHNICAL", True)
        if isinstance(exc, MessagingAuthenticationError):
            return FailureDecision("AUTHENTICATION", False)
        if isinstance(exc, MessagingConfigurationError):
            return FailureDecision("CONFIGURATION", False)
        if isinstance(exc, MessagingPermanentError):
            return FailureDecision("PERMANENT_TECHNICAL", False)
        if isinstance(exc, IntegrityError):
            return FailureDecision("PERMANENT_TECHNICAL", False)
        # Database operational failures are intentionally not retried blindly:
        # a caller must translate a known deadlock/serialization failure first.
        if isinstance(exc, OperationalError):
            return FailureDecision("UNKNOWN", False)
        if isinstance(exc, MessagingError):
            return FailureDecision("RETRYABLE_TECHNICAL" if exc.retryable else "PERMANENT_TECHNICAL", exc.retryable)
        return FailureDecision("UNKNOWN", False)


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
        delay = min(settings.NOTIFICATION_DELIVERY_RETRY_BASE_SECONDS * 2 ** (attempt_number - 1), maximum)
        if settings.NOTIFICATION_DELIVERY_RETRY_JITTER:
            return self.randomizer.randint(max(1, delay // 2), delay)
        return delay
