class ParentSquareError(RuntimeError):
    """Base error for the ParentSquare client."""


class AuthenticationError(ParentSquareError):
    """Credentials or the current session are not accepted."""


class MFARequired(ParentSquareError):
    """The account requires a one-time verification code."""

    def __init__(self, contact_method: str, contact_hint: str):
        self.contact_method = contact_method
        self.contact_hint = contact_hint
        super().__init__(
            f"ParentSquare MFA code required via {contact_method} ({contact_hint})"
        )


class RateLimitError(ParentSquareError):
    """ParentSquare asked the caller to slow down."""

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        message = "ParentSquare rate limit reached"
        if retry_after is not None:
            message += f"; retry after {retry_after:g} seconds"
        super().__init__(message)
