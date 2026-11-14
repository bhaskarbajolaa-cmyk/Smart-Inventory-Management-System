"""In-memory authorization-code and refresh-token storage.

State is process-local and deliberately not persisted: the provider is a demo
identity service. A production deployment would swap this class for a database
or Redis implementation with the same methods.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import secrets
import threading
import time

from inventory.auth_server.errors import OAuthError


@dataclass
class AuthorizationCode:
    """A single-use authorization code, bound to its client and redirect URI."""

    code: str
    client_id: str
    redirect_uri: str
    subject: str
    scope: str
    nonce: str | None
    code_challenge: str | None
    code_challenge_method: str | None
    access_type: str
    expires_at: float
    used: bool = False


@dataclass
class RefreshToken:
    """A long-lived token that can mint new access tokens."""

    token: str
    client_id: str
    subject: str
    scope: str
    expires_at: float


def verify_pkce(
    challenge: str | None,
    method: str | None,
    verifier: str | None,
) -> bool:
    """Validate a PKCE ``code_verifier`` against the stored challenge (RFC 7636)."""
    if not challenge:
        return True
    if not verifier:
        return False
    if method == "S256":
        try:
            digest = hashlib.sha256(verifier.encode("ascii")).digest()
        except UnicodeEncodeError:
            return False
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(computed, challenge)
    if method in (None, "", "plain"):
        return secrets.compare_digest(verifier, challenge)
    return False


class InMemoryAuthorizationStore:
    """Thread-safe code and refresh-token storage with TTL enforcement."""

    def __init__(
        self,
        *,
        code_ttl_seconds: int = 300,
        refresh_ttl_seconds: int = 86400,
        clock=time.time,
    ) -> None:
        if code_ttl_seconds <= 0 or refresh_ttl_seconds <= 0:
            raise ValueError("Token lifetimes must be positive.")
        self._code_ttl = code_ttl_seconds
        self._refresh_ttl = refresh_ttl_seconds
        self._clock = clock
        self._codes: dict[str, AuthorizationCode] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Authorization codes
    # ------------------------------------------------------------------

    def issue_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        subject: str,
        scope: str,
        nonce: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
        access_type: str = "online",
    ) -> str:
        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            subject=subject,
            scope=scope,
            nonce=nonce,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            access_type=access_type,
            expires_at=self._clock() + self._code_ttl,
        )
        with self._lock:
            self._purge_locked()
            self._codes[code] = record
        return code

    def consume_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
    ) -> AuthorizationCode:
        """Return the code once, then invalidate it.

        Every failure mode is reported as ``invalid_grant`` so the caller cannot
        probe which part of the code was wrong.
        """
        with self._lock:
            self._purge_locked()
            record = self._codes.get(code)
            if record is None:
                raise OAuthError(
                    "invalid_grant", "Authorization code is unknown or expired."
                )
            if record.used:
                raise OAuthError(
                    "invalid_grant", "Authorization code has already been used."
                )
            if record.expires_at <= self._clock():
                raise OAuthError("invalid_grant", "Authorization code has expired.")
            if record.client_id != client_id:
                raise OAuthError(
                    "invalid_grant", "Authorization code was issued to another client."
                )
            if record.redirect_uri != redirect_uri:
                raise OAuthError(
                    "invalid_grant", "redirect_uri does not match the authorization request."
                )
            record.used = True
            return record

    # ------------------------------------------------------------------
    # Refresh tokens
    # ------------------------------------------------------------------

    def issue_refresh_token(
        self,
        *,
        client_id: str,
        subject: str,
        scope: str,
    ) -> str:
        token = secrets.token_urlsafe(48)
        record = RefreshToken(
            token=token,
            client_id=client_id,
            subject=subject,
            scope=scope,
            expires_at=self._clock() + self._refresh_ttl,
        )
        with self._lock:
            self._purge_locked()
            self._refresh_tokens[token] = record
        return token

    def get_refresh_token(self, token: str, client_id: str) -> RefreshToken:
        """Look up a refresh token without consuming it."""
        with self._lock:
            self._purge_locked()
            record = self._refresh_tokens.get(token)
            if record is None:
                raise OAuthError("invalid_grant", "Refresh token is unknown or expired.")
            if record.expires_at <= self._clock():
                raise OAuthError("invalid_grant", "Refresh token has expired.")
            if record.client_id != client_id:
                raise OAuthError(
                    "invalid_grant", "Refresh token was issued to another client."
                )
            return record

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _purge_locked(self) -> None:
        now = self._clock()
        for code in [key for key, item in self._codes.items() if item.expires_at <= now]:
            del self._codes[code]
        for token in [
            key for key, item in self._refresh_tokens.items() if item.expires_at <= now
        ]:
            del self._refresh_tokens[token]

    def purge(self) -> None:
        """Drop every expired record (also called lazily on each operation)."""
        with self._lock:
            self._purge_locked()

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._purge_locked()
            return {
                "authorization_codes": len(self._codes),
                "refresh_tokens": len(self._refresh_tokens),
            }
