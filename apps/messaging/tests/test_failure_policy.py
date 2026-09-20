from django.db import IntegrityError
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.messaging.application.failures import (
    FailureClassifier,
    NotificationRetryPolicy,
)
from apps.messaging.domain.exceptions import (
    MessagingAuthenticationError,
    MessagingPermanentError,
    MessagingRateLimitError,
    MessagingTemporaryError,
)
from apps.telegram.exceptions import TelegramRateLimitError
from apps.whatsapp.domain.exceptions import WhatsAppTemplateConfigurationError
from apps.whatsapp.exceptions import WhatsAppAPIError


def test_provider_failures_are_classified_without_business_outcomes():
    assert FailureClassifier.classify(MessagingTemporaryError("timeout")).retryable
    rate_limited = FailureClassifier.classify(MessagingRateLimitError("slow", retry_after=12))
    assert rate_limited.category == "RATE_LIMITED"
    assert rate_limited.retry_after == 12
    assert not FailureClassifier.classify(MessagingPermanentError("bad request")).retryable
    assert FailureClassifier.classify(MessagingAuthenticationError("bad token")).category == "AUTHENTICATION"
    assert not FailureClassifier.classify(IntegrityError("constraint")).retryable
    assert FailureClassifier.classify(RedisConnectionError("redis down")).retryable
    assert FailureClassifier.classify(WhatsAppAPIError("outage", retryable=True)).retryable
    assert FailureClassifier.classify(TelegramRateLimitError("slow", retry_after=9)).retry_after == 9
    assert FailureClassifier.classify(WhatsAppTemplateConfigurationError("missing")).category == "CONFIGURATION"


def test_backoff_is_bounded_and_honours_retry_after(settings):
    settings.NOTIFICATION_DELIVERY_RETRY_BASE_SECONDS = 10
    settings.NOTIFICATION_DELIVERY_RETRY_MAX_SECONDS = 60
    settings.NOTIFICATION_DELIVERY_RETRY_JITTER = False
    policy = NotificationRetryPolicy()
    assert policy.countdown(1) == 10
    assert policy.countdown(4) == 60
    assert policy.countdown(1, retry_after=1000) == 60
