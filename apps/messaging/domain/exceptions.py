class MessagingError(Exception):
    retryable = False


class MessagingConfigurationError(MessagingError):
    pass


class MessagingChannelDisabled(MessagingConfigurationError):
    pass


class MessagingAuthenticationError(MessagingError):
    pass


class MessagingRateLimitError(MessagingError):
    retryable = True

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class MessagingTemporaryError(MessagingError):
    retryable = True


class MessagingPermanentError(MessagingError):
    pass


class UnsupportedMessagingCapability(MessagingPermanentError):
    pass
