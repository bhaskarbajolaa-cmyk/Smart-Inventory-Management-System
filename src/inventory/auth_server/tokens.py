"""JWT creation and verification helpers for the identity provider."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from inventory.auth_server.keys import SigningKey

try:  # pragma: no cover - exercised through the ImportError message
    import jwt

    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JWT_AVAILABLE = False


class TokenError(RuntimeError):
    """Raised when a token cannot be created or decoded."""


def _require_jwt() -> None:
    if not _JWT_AVAILABLE:
        raise TokenError(
            "PyJWT is not installed. Run: pip install \"PyJWT[crypto]>=2.8\""
        )


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def at_hash(access_token: str) -> str:
    """OIDC ``at_hash``: left half of the SHA-256 digest, base64url encoded."""
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return _base64url(digest[: len(digest) // 2])


def encode_signed_jwt(claims: dict[str, Any], key: SigningKey) -> str:
    """Sign ``claims`` with the provider key and publish the ``kid`` header."""
    _require_jwt()
    return jwt.encode(
        claims,
        key.private_pem,
        algorithm=key.algorithm,
        headers={"kid": key.kid},
    )


def build_access_token(
    *,
    key: SigningKey,
    issuer: str,
    client_id: str,
    subject: str,
    scope: str,
    issued_at: int,
    expires_at: int,
) -> str:
    """Create the bearer token used by the userinfo endpoint.

    The token is a signed JWT rather than an opaque string so the provider can
    validate it statelessly. ``token_use`` distinguishes it from an ID token.
    """
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": client_id,
        "azp": client_id,
        "sub": subject,
        "scope": scope,
        "token_use": "access",
        "iat": int(issued_at),
        "exp": int(expires_at),
    }
    return encode_signed_jwt(claims, key)


def build_id_token(
    *,
    key: SigningKey,
    issuer: str,
    audience: str,
    subject: str,
    email: str,
    email_verified: bool,
    name: str = "",
    picture: str = "",
    issued_at: int,
    expires_at: int,
    nonce: str | None = None,
    access_token: str | None = None,
) -> str:
    """Create an OpenID Connect ID token with Google-shaped claims."""
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "azp": audience,
        "sub": subject,
        "email": email,
        "email_verified": bool(email_verified),
        "iat": int(issued_at),
        "exp": int(expires_at),
    }
    if name:
        claims["name"] = name
    if picture:
        claims["picture"] = picture
    if nonce:
        claims["nonce"] = nonce
    if access_token:
        claims["at_hash"] = at_hash(access_token)
    return encode_signed_jwt(claims, key)


def decode_access_token(
    token: str,
    key: SigningKey,
    issuer: str,
    *,
    leeway_seconds: int = 60,
) -> dict[str, Any]:
    """Validate a provider-issued access token for the userinfo endpoint."""
    _require_jwt()
    try:
        claims = jwt.decode(
            token,
            key.public_pem,
            algorithms=[key.algorithm],
            issuer=issuer,
            leeway=leeway_seconds,
            options={
                "verify_aud": False,
                "require": ["exp", "iat", "iss", "sub"],
            },
        )
    except jwt.PyJWTError as error:
        raise TokenError(str(error)) from error

    if claims.get("token_use") != "access":
        raise TokenError("The token is not an access token.")
    return claims
