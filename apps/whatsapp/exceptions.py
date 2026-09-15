class WhatsAppError(Exception):
    retryable = False


class WhatsAppConfigurationError(WhatsAppError):
    pass


class WhatsAppAPIError(WhatsAppError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_data: dict | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}
        self.retryable = retryable


class WhatsAppAuthenticationError(WhatsAppAPIError):
    pass


class WhatsAppRateLimitError(WhatsAppAPIError):
    retryable = True


class WhatsAppInvalidPayloadError(WhatsAppError):
    pass
