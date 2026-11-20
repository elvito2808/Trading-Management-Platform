"""Structured errors for exchange integrations."""


class ExchangeError(Exception):
    pass


class ExchangeNetworkError(ExchangeError):
    pass


class ExchangeTimeoutError(ExchangeNetworkError):
    pass


class ExchangeAuthenticationError(ExchangeError):
    pass


class ExchangeRateLimitError(ExchangeError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ExchangeHTTPError(ExchangeError):
    pass


class ExchangeResponseError(ExchangeError):
    pass
