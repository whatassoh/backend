class AIError(Exception):
    """Base error for the AI boundary."""

    retryable = False


class AIConfigurationError(AIError):
    """AI is disabled or its selected provider is not configured."""


class AIProviderError(AIError):
    """A permanent provider-side failure."""


class AIAuthenticationError(AIProviderError):
    """The provider rejected the configured credentials."""


class AIRateLimitError(AIProviderError):
    retryable = True


class AITimeoutError(AIProviderError):
    retryable = True


class AITemporaryError(AIProviderError):
    retryable = True


class AIInvalidResponseError(AIProviderError):
    """The provider response is empty, unparsable, or violates the schema."""
