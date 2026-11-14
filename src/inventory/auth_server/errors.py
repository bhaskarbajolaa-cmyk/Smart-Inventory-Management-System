"""OAuth 2.0 / OpenID Connect errors returned by the provider endpoints."""

from __future__ import annotations


class OAuthError(Exception):
    """An RFC 6749 error payload for the token or authorization endpoint.

    ``error`` and ``description`` map directly to the ``error`` and
    ``error_description`` members of the JSON error response. ``status_code``
    is the HTTP status to return, and ``headers`` carries protocol headers such
    as ``WWW-Authenticate``.
    """

    def __init__(
        self,
        error: str,
        description: str = "",
        *,
        status_code: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status_code = status_code
        self.headers = headers or {}
