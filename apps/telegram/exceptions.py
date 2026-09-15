class TelegramError(Exception):
    retryable = False


class TelegramConfigurationError(TelegramError):
    pass


class TelegramAPIError(TelegramError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_data: dict | None = None,
        retryable: bool = False,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}
        self.retryable = retryable
        self.retry_after = retry_after


class TelegramAuthenticationError(TelegramAPIError):
    pass


class TelegramRateLimitError(TelegramAPIError):
    retryable = True


class TelegramInvalidPayloadError(TelegramError):
    pass
