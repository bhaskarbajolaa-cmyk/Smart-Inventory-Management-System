"""Verification of Google-style OpenID Connect ID tokens.

The verifier deliberately trusts nothing in the token until PyJWT has checked
the RS256 signature against a published JWKS key, the issuer, the audience and
the expiry. Decoding a token without those checks (the pattern used by
``Session.decode_token`` for the app's own opaque tokens) would let anyone mint
an administrator session, so it is not repeated here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

try:  # pragma: no cover - the ImportError path is covered by a unit test
    import jwt

    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JWT_AVAILABLE = False


DEFAULT_ISSUERS: tuple[str, ...] = (
    "https://accounts.google.com",
    "accounts.google.com",
)
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256",)


class GoogleTokenError(ValueError):
    """Base class for every rejected Google ID token."""


class GoogleTokenInvalid(GoogleTokenError):
    """The token is malformed, has the wrong claims, or fails verification."""


class GoogleTokenExpired(GoogleTokenError):
    """The token's ``exp`` claim is in the past."""


class GoogleKeyError(GoogleTokenError):
    """The signing key could not be resolved from the JWKS document."""


@dataclass(frozen=True)
class GoogleIdentity:
    """The verified identity asserted by an ID token."""

    subject: str
    email: str
    email_verified: bool
    name: str
    picture: str
    issuer: str
    audience: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    nonce: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def display_name(self) -> str:
        return self.name or self.email or self.subject


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _string_claim(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    return value if isinstance(value, str) else ""


class GoogleTokenVerifier:
    """Verify ID tokens issued by Google, or by any Google-compatible provider.

    Args:
        audience: the OAuth client id(s) the token must be addressed to.
        issuers: acceptable ``iss`` values. Defaults to Google's two spellings.
        jwks: an already-fetched JWKS document (or ``{kid: jwk}`` mapping). When
            supplied, no network request is made — this is how tests and
            offline deployments pin keys.
        jwks_url: where to fetch keys when ``jwks`` is absent.
        algorithms: accepted signing algorithms. Keep this at RS256; accepting
            ``none`` or an HMAC algorithm here is the classic key-confusion bug.
    """

    def __init__(
        self,
        audience: str | Iterable[str],
        *,
        issuers: str | Iterable[str] = DEFAULT_ISSUERS,
        jwks: Mapping[str, Any] | None = None,
        jwks_url: str | None = GOOGLE_JWKS_URL,
        algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
        leeway_seconds: int = 60,
        require_email_verified: bool = True,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: int = 3600,
        min_refresh_interval_seconds: float = 5.0,
        opener=None,
    ) -> None:
        audiences = (audience,) if isinstance(audience, str) else tuple(audience)
        if not audiences or not all(isinstance(item, str) and item for item in audiences):
            raise ValueError("audience must be a non-empty string or iterable of strings.")
        allowed_issuers = (issuers,) if isinstance(issuers, str) else tuple(issuers)
        if not allowed_issuers:
            raise ValueError("At least one acceptable issuer is required.")
        if not algorithms:
            raise ValueError("At least one signing algorithm is required.")
        if leeway_seconds < 0:
            raise ValueError("leeway_seconds must be non-negative.")

        self.audience = audiences
        self.issuers = allowed_issuers
        self.algorithms = tuple(algorithms)
        self.leeway_seconds = leeway_seconds
        self.require_email_verified = require_email_verified
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.min_refresh_interval_seconds = min_refresh_interval_seconds

        self._jwks_url = jwks_url
        self._opener = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._loaded = False
        if jwks is not None:
            self._keys = self._normalise_jwks(jwks)
            self._fetched_at = time.monotonic()
            self._loaded = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(self, token: str) -> GoogleIdentity:
        """Validate ``token`` and return the identity it asserts."""
        if not _JWT_AVAILABLE:
            raise GoogleTokenError(
                "PyJWT is not installed. Run: pip install \"PyJWT[crypto]>=2.8\""
            )
        if not isinstance(token, str) or not token.strip():
            raise GoogleTokenInvalid("An ID token is required.")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise GoogleTokenInvalid("The ID token is not a well-formed JWT.") from error

        algorithm = header.get("alg")
        if algorithm not in self.algorithms:
            raise GoogleTokenInvalid(
                f"Unsupported signing algorithm {algorithm!r}; expected one of "
                f"{', '.join(self.algorithms)}."
            )
        kid = header.get("kid")
        public_key = self._resolve_key(kid if isinstance(kid, str) else None)

        try:
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=list(self.algorithms),
                audience=list(self.audience),
                issuer=list(self.issuers),
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as error:
            raise GoogleTokenExpired("The ID token has expired.") from error
        except jwt.InvalidAudienceError as error:
            raise GoogleTokenInvalid(
                "The ID token audience does not match this application."
            ) from error
        except jwt.InvalidIssuerError as error:
            raise GoogleTokenInvalid(
                "The ID token was issued by an unexpected issuer."
            ) from error
        except jwt.InvalidSignatureError as error:
            raise GoogleTokenInvalid("The ID token signature is invalid.") from error
        except jwt.ImmatureSignatureError as error:
            raise GoogleTokenInvalid("The ID token is not valid yet.") from error
        except jwt.MissingRequiredClaimError as error:
            raise GoogleTokenInvalid(
                f"The ID token is missing the {error.claim} claim."
            ) from error
        except jwt.PyJWTError as error:
            raise GoogleTokenInvalid(f"The ID token was rejected: {error}") from error

        return self._to_identity(claims)

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_jwks(document: Mapping[str, Any]) -> dict[str, Any]:
        """Accept a JWKS document or a ``{kid: jwk}`` mapping."""
        if not isinstance(document, Mapping):
            raise GoogleKeyError("The JWKS payload must be a JSON object.")

        entries: list[Mapping[str, Any]] = []
        if "keys" in document:
            keys = document.get("keys")
            if not isinstance(keys, list):
                raise GoogleKeyError("The JWKS document has no usable 'keys' array.")
            entries = [entry for entry in keys if isinstance(entry, Mapping)]
        elif all(isinstance(value, Mapping) for value in document.values()):
            entries = [entry for entry in document.values() if isinstance(entry, Mapping)]
        else:
            raise GoogleKeyError("The JWKS payload is not a key set.")

        if not entries:
            raise GoogleKeyError("The JWKS document contains no keys.")

        keys_by_id: dict[str, Any] = {}
        for entry in entries:
            if entry.get("kty") != "RSA":
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid:
                raise GoogleKeyError("Every signing key must carry a 'kid'.")
            if entry.get("use") not in (None, "sig"):
                continue
            try:
                keys_by_id[kid] = jwt.PyJWK(entry, algorithm="RS256").key
            except jwt.PyJWTError as error:
                raise GoogleKeyError(f"Unusable signing key {kid!r}: {error}") from error

        if not keys_by_id:
            raise GoogleKeyError("The JWKS document contains no usable RSA signing keys.")
        return keys_by_id

    def _resolve_key(self, kid: str | None) -> Any:
        with self._lock:
            if not self._loaded:
                self._refresh_locked()
            if kid and kid in self._keys:
                return self._keys[kid]
            if kid is None and len(self._keys) == 1:
                return next(iter(self._keys.values()))

            # Unknown key id: refresh once, but never more often than the
            # minimum interval, so a crafted `kid` cannot hammer the JWKS URL.
            if self._jwks_url and (
                time.monotonic() - self._fetched_at >= self.min_refresh_interval_seconds
            ):
                self._refresh_locked(force=True)
                if kid and kid in self._keys:
                    return self._keys[kid]

            known = ", ".join(sorted(self._keys)) or "none"
            raise GoogleKeyError(
                f"No signing key matches kid {kid!r}; available: {known}."
            )

    def _refresh_locked(self, force: bool = False) -> None:
        """Fetch the JWKS document. The caller must hold ``self._lock``."""
        if not self._jwks_url:
            raise GoogleKeyError("No JWKS document or JWKS URL is configured.")
        age = time.monotonic() - self._fetched_at
        if not force and self._loaded and age < self.cache_ttl_seconds:
            return

        request = urllib.request.Request(
            self._jwks_url,
            headers={"Accept": "application/json", "User-Agent": "inventory-oidc/1.0"},
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise GoogleKeyError(
                        f"JWKS endpoint returned HTTP {status}."
                    )
                payload = json.loads(response.read().decode("utf-8"))
        except GoogleTokenError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GoogleKeyError(f"Could not fetch the JWKS document: {error}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GoogleKeyError("The JWKS endpoint returned invalid JSON.") from error

        self._keys = self._normalise_jwks(payload)
        self._fetched_at = time.monotonic()
        self._loaded = True

    # ------------------------------------------------------------------
    # Claim mapping
    # ------------------------------------------------------------------

    def _to_identity(self, claims: Mapping[str, Any]) -> GoogleIdentity:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise GoogleTokenInvalid("The ID token has no subject.")

        email = _string_claim(claims, "email")
        email_verified = _as_bool(claims.get("email_verified"))
        if self.require_email_verified:
            if not email:
                raise GoogleTokenInvalid("The ID token has no email claim.")
            if not email_verified:
                raise GoogleTokenInvalid(
                    "The email address asserted by the ID token is not verified."
                )

        audience_claim = claims.get("aud")
        if isinstance(audience_claim, list):
            audience = tuple(str(item) for item in audience_claim)
        else:
            audience = (str(audience_claim),)

        try:
            issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc)
            expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise GoogleTokenInvalid("The ID token has invalid timestamps.") from error

        nonce = claims.get("nonce")
        return GoogleIdentity(
            subject=subject,
            email=email,
            email_verified=email_verified,
            name=_string_claim(claims, "name"),
            picture=_string_claim(claims, "picture"),
            issuer=str(claims.get("iss", "")),
            audience=audience,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce if isinstance(nonce, str) else None,
            claims=dict(claims),
        )


def verify_google_id_token(
    token: str,
    audience: str | Iterable[str],
    **kwargs: Any,
) -> GoogleIdentity:
    """Convenience wrapper around :class:`GoogleTokenVerifier`."""
    return GoogleTokenVerifier(audience, **kwargs).verify(token)
