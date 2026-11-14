"""Authorization-code + PKCE client for the Google-style OpenID Connect provider.

:mod:`inventory.auth.google` answers "is this ID token genuine?"; this module
answers the other half: "how do we obtain one without trusting the browser?".
It implements the client side of the flow documented in ``docs/GOOGLE_OIDC.md``.
:meth:`GoogleLoginService.start` mints a single-use ``state``, a PKCE
``code_verifier`` and a nonce, and :meth:`GoogleLoginService.complete` redeems
the returned authorization code at the token endpoint before verifying the ID
token against the configured JWKS document.

Only the server-side ``state`` record holds the verifier and nonce, and it is
dropped the moment the code is redeemed, so a replayed callback cannot succeed.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from inventory.auth.google import GoogleIdentity, GoogleTokenError, GoogleTokenVerifier

__all__ = [
    "GoogleLoginConfig",
    "GoogleLoginError",
    "GoogleLoginResult",
    "GoogleLoginService",
]

DEFAULT_CLIENT_ID = "smobhar-inventory"
DEFAULT_ISSUER = "http://127.0.0.1:5001"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:5000/api/auth/google/callback"
DEFAULT_SCOPES: tuple[str, ...] = ("openid", "email", "profile")
DEFAULT_ROLE_ID = 3

AUTHORIZE_PATH = "/o/oauth2/v2/auth"
TOKEN_PATH = "/token"
JWKS_PATH = "/oauth2/v3/certs"

#: The provider is local during development, so a short timeout is enough.
TOKEN_REQUEST_TIMEOUT = 5.0

#: Provider messages are truncated so a hostile body cannot flood a log page.
MAX_ERROR_LENGTH = 300

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class GoogleLoginError(RuntimeError):
    """Raised when a login attempt cannot be started or completed.

    Messages are safe to show to the user and to log: they never contain the
    authorization code, the PKCE verifier, a token, or the client secret.
    """


@dataclass(frozen=True)
class GoogleLoginConfig:
    """Everything the login service needs to talk to the provider."""

    client_id: str
    client_secret: str | None
    issuer: str
    redirect_uri: str
    authorize_endpoint: str
    token_endpoint: str
    jwks_url: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    auto_provision: bool = True
    default_role_id: int = 3

    @classmethod
    def from_environment(cls) -> "GoogleLoginConfig":
        """Build a configuration from the ``GOOGLE_*`` environment variables.

        The endpoints are derived from ``GOOGLE_ISSUER`` because the bundled
        provider mirrors Google's paths, which keeps a single variable in
        charge of where the login flow points.
        """
        client_id = _env_text("GOOGLE_CLIENT_ID", DEFAULT_CLIENT_ID)
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        issuer = _env_text("GOOGLE_ISSUER", DEFAULT_ISSUER).rstrip("/") or DEFAULT_ISSUER
        redirect_uri = _env_text("GOOGLE_REDIRECT_URI", DEFAULT_REDIRECT_URI)
        scopes = tuple(os.environ.get("GOOGLE_SCOPES", "").split()) or DEFAULT_SCOPES

        return cls(
            client_id=client_id,
            client_secret=client_secret or None,
            issuer=issuer,
            redirect_uri=redirect_uri,
            authorize_endpoint=issuer + AUTHORIZE_PATH,
            token_endpoint=issuer + TOKEN_PATH,
            jwks_url=issuer + JWKS_PATH,
            scopes=scopes,
            auto_provision=_env_flag("GOOGLE_AUTO_PROVISION", True),
            default_role_id=_env_int("GOOGLE_DEFAULT_ROLE_ID", DEFAULT_ROLE_ID),
        )


@dataclass(frozen=True)
class GoogleLoginResult:
    """The outcome of a successful code exchange."""

    id_token: str
    access_token: str
    scope: str
    expires_in: int | None
    identity: GoogleIdentity


@dataclass(frozen=True)
class _PendingLogin:
    """Server-side state for one in-flight authorization request."""

    code_verifier: str
    nonce: str
    return_to: str
    created_at: float
    redirect_uri: str


class GoogleLoginService:
    """Run the authorization-code flow against a Google-compatible provider.

    Args:
        config: endpoints, client registration and provisioning defaults.
        verifier: an already-configured :class:`GoogleTokenVerifier`. Supplying
            one keeps the exchange offline (tests, pinned-key deployments);
            otherwise it is built lazily from the config.
        opener: callable with the ``urllib.request.urlopen`` signature. It is
            called as ``opener(request, timeout=5.0)``, so tests can inject a
            fake token endpoint.
        state_ttl_seconds: how long a started login may take to come back.
        clock: source of ``time.time()``-style timestamps; injectable so expiry
            can be tested without sleeping.
    """

    def __init__(
        self,
        config: GoogleLoginConfig,
        *,
        verifier: GoogleTokenVerifier | None = None,
        opener: Callable[..., Any] | None = None,
        state_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(config, GoogleLoginConfig):
            raise TypeError("config must be a GoogleLoginConfig instance.")
        if state_ttl_seconds <= 0:
            raise ValueError("state_ttl_seconds must be positive.")
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self._config = config
        self._verifier = verifier
        self._opener = opener or urllib.request.urlopen
        self._state_ttl_seconds = state_ttl_seconds
        self._clock = clock
        self._pending: dict[str, _PendingLogin] = {}
        self._pending_lock = threading.Lock()
        self._verifier_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> GoogleLoginConfig:
        """The configuration this service was built with."""
        return self._config

    @property
    def verifier(self) -> GoogleTokenVerifier:
        """The ID-token verifier, created on first use when not injected."""
        with self._verifier_lock:
            if self._verifier is None:
                self._verifier = GoogleTokenVerifier(
                    self._config.client_id,
                    issuers=(self._config.issuer,),
                    jwks_url=self._config.jwks_url,
                )
            return self._verifier

    def start(
        self,
        *,
        return_to: str = "",
        redirect_uri: str | None = None,
    ) -> tuple[str, str]:
        """Begin a login and return ``(authorize_url, state)``.

        ``return_to`` stays on this side: it is stored with the pending login so
        the callback can redirect the user back, and is deliberately never sent
        to the provider.

        ``redirect_uri`` overrides the configured callback URL for this request.
        The server uses it when the browser is on a different spelling of the
        same host (``localhost`` vs ``127.0.0.1``), so the state cookie set at
        ``/start`` is still present at the callback. The value is remembered on
        the pending login and reused for the token exchange, which must match.
        """
        config = self._config
        if not config.client_id:
            raise GoogleLoginError("GOOGLE_CLIENT_ID is not configured.")
        if not config.authorize_endpoint:
            raise GoogleLoginError("The authorization endpoint is not configured.")

        effective_redirect = (redirect_uri or config.redirect_uri or "").strip()
        if not effective_redirect:
            raise GoogleLoginError("The redirect URI is not configured.")

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(32)
        now = self._clock()

        with self._pending_lock:
            self._purge_locked(now)
            while state in self._pending:  # pragma: no cover - 256-bit collision
                state = secrets.token_urlsafe(32)
            self._pending[state] = _PendingLogin(
                code_verifier=code_verifier,
                nonce=nonce,
                return_to=return_to if isinstance(return_to, str) else "",
                created_at=now,
                redirect_uri=effective_redirect,
            )

        params = {
            "client_id": config.client_id,
            "redirect_uri": effective_redirect,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": _code_challenge(code_verifier),
            "code_challenge_method": "S256",
            "access_type": "online",
        }
        separator = "&" if urllib.parse.urlsplit(config.authorize_endpoint).query else "?"
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        return config.authorize_endpoint + separator + query, state

    def complete(self, state: str, code: str) -> GoogleLoginResult:
        """Redeem ``code`` for the login identified by ``state``.

        The pending record is popped before the token request, so a state is
        single-use even when the exchange fails.
        """
        if not isinstance(state, str) or not state:
            raise GoogleLoginError("The login state is missing.")
        if not isinstance(code, str) or not code:
            raise GoogleLoginError("The authorization code is missing.")

        now = self._clock()
        with self._pending_lock:
            pending = self._pending.pop(state, None)
            self._purge_locked(now)

        if pending is None:
            raise GoogleLoginError(
                "The login attempt is unknown or has expired. Start again."
            )
        if now - pending.created_at > self._state_ttl_seconds:
            raise GoogleLoginError("The login attempt has expired. Start again.")

        def fail(message: str) -> GoogleLoginError:
            """Build an error whose text cannot echo credentials."""
            return GoogleLoginError(
                _scrub(message, code, pending.code_verifier, self._config.client_secret)
            )

        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": self._config.client_id,
            "code_verifier": pending.code_verifier,
        }
        if self._config.client_secret:
            fields["client_secret"] = self._config.client_secret

        request = urllib.request.Request(
            self._config.token_endpoint,
            data=urllib.parse.urlencode(fields).encode("ascii"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        try:
            response = self._opener(request, timeout=TOKEN_REQUEST_TIMEOUT)
        except urllib.error.HTTPError as error:
            # The provider sends its OAuth error document with a 4xx status.
            provider_error = _provider_message(_read_json(error))
            message = provider_error or f"The token endpoint returned HTTP {error.code}."
            raise fail(message) from None
        except urllib.error.URLError as error:
            raise fail(f"The token endpoint could not be reached: {error.reason}.") from None
        except (TimeoutError, OSError):
            raise fail("The token endpoint could not be reached.") from None

        try:
            status, payload = _read_response(response)
        except (OSError, ValueError):
            raise fail("The token endpoint returned an unreadable response.") from None

        if status != 200:
            message = (
                _provider_message(payload) or f"The token endpoint returned HTTP {status}."
            )
            raise fail(message)

        if not isinstance(payload, Mapping):
            raise fail("The token endpoint returned an invalid response.")
        provider_error = _provider_message(payload)
        if provider_error:
            raise fail(provider_error)

        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token.strip():
            raise fail("The token response did not include an ID token.")

        try:
            identity = self.verifier.verify(id_token)
        except GoogleTokenError as error:
            raise fail(f"The ID token could not be verified: {error}") from None

        if identity.nonce != pending.nonce:
            raise fail("The ID token does not belong to this login attempt (nonce mismatch).")

        return GoogleLoginResult(
            id_token=id_token,
            access_token=_as_text(payload.get("access_token")),
            scope=_as_text(payload.get("scope")) or " ".join(self._config.scopes),
            expires_in=_as_int(payload.get("expires_in")),
            identity=identity,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _purge_locked(self, now: float) -> None:
        """Drop expired pending logins. The caller must hold ``_pending_lock``."""
        expired = [
            key
            for key, pending in self._pending.items()
            if now - pending.created_at > self._state_ttl_seconds
        ]
        for key in expired:
            self._pending.pop(key, None)


def _env_text(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value not in _FALSE_VALUES


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}.") from None


def _code_challenge(code_verifier: str) -> str:
    """PKCE S256 challenge: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _read_response(response: Any) -> tuple[int, Any]:
    """Return ``(status, payload)`` for a normal or context-manager response."""
    if hasattr(response, "__enter__"):
        with response as stream:
            return _response_status(stream), _read_json(stream)
    try:
        return _response_status(response), _read_json(response)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _response_status(response: Any) -> int:
    """Read the HTTP status, tolerating minimal test doubles."""
    status = getattr(response, "status", None)
    if isinstance(status, str) and status.strip().isdigit():
        status = int(status.strip())
    if not isinstance(status, int):
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else None
    return status if isinstance(status, int) else 200


def _read_json(response: Any) -> Any:
    """Read and decode a JSON body, returning ``None`` for anything unusable."""
    try:
        raw = response.read()
    except (OSError, ValueError):
        return None

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(raw, str):
        text = raw
    else:
        return None

    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _provider_message(payload: Any) -> str | None:
    """Turn an OAuth error document into one readable sentence."""
    if not isinstance(payload, Mapping):
        return None

    error = payload.get("error")
    code = error.strip() if isinstance(error, str) else ""
    description = payload.get("error_description")
    if not isinstance(description, str) or not description.strip():
        description = payload.get("error_message")

    if isinstance(error, Mapping):  # e.g. {"error": {"code": 400, "message": ...}}
        nested = error.get("message")
        if isinstance(nested, str) and nested.strip():
            description = description if isinstance(description, str) else nested
        code = code or str(error.get("status") or error.get("code") or "")

    if isinstance(description, str) and description.strip():
        text = f"The provider rejected the login: {description.strip()}"
        return f"{text} ({code})" if code else text
    if code:
        return f"The provider rejected the login: {code}"
    return None


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _scrub(message: str, *secrets_to_hide: str | None) -> str:
    """Collapse whitespace, redact credentials and cap the message length."""
    text = " ".join(str(message).split())
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, "[redacted]")
    if len(text) > MAX_ERROR_LENGTH:
        text = text[: MAX_ERROR_LENGTH - 3].rstrip() + "..."
    return text or "The login could not be completed."
