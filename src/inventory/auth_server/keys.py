"""RSA signing keys and the JWKS document served to token consumers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised through the ImportError message
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CRYPTO_AVAILABLE = False


class SigningKeyError(RuntimeError):
    """Raised when a signing key cannot be created or loaded."""


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_uint(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return _base64url(value.to_bytes(length, "big"))


@dataclass(frozen=True)
class SigningKey:
    """An RSA key pair plus the ``kid`` published in the JWKS document."""

    private_pem: bytes
    public_pem: bytes
    kid: str
    algorithm: str = "RS256"

    def jwk(self) -> dict[str, str]:
        """Return the public half as a JWK usable by any OIDC client."""
        public_key = serialization.load_pem_public_key(self.public_pem)
        numbers = public_key.public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": self.algorithm,
            "kid": self.kid,
            "n": _base64url_uint(numbers.n),
            "e": _base64url_uint(numbers.e),
        }


def _thumbprint(public_numbers: Any) -> str:
    """RFC 7638 JWK thumbprint, used as a stable key id."""
    canonical = json.dumps(
        {
            "e": _base64url_uint(public_numbers.e),
            "kty": "RSA",
            "n": _base64url_uint(public_numbers.n),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return _base64url(hashlib.sha256(canonical.encode("utf-8")).digest())


def _build_key(private_key: Any) -> SigningKey:
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return SigningKey(
        private_pem=private_pem,
        public_pem=public_pem,
        kid=_thumbprint(private_key.public_key().public_numbers()),
    )


def generate_signing_key(bits: int = 2048) -> SigningKey:
    """Create an ephemeral RSA key pair (development and tests)."""
    if not _CRYPTO_AVAILABLE:
        raise SigningKeyError(
            "cryptography is not installed. Run: pip install \"PyJWT[crypto]>=2.8\""
        )
    if bits < 2048:
        raise SigningKeyError("RSA keys must be at least 2048 bits.")
    return _build_key(rsa.generate_private_key(public_exponent=65537, key_size=bits))


def load_signing_key(path: str | Path) -> SigningKey:
    """Load a PEM-encoded RSA private key from disk."""
    if not _CRYPTO_AVAILABLE:
        raise SigningKeyError(
            "cryptography is not installed. Run: pip install \"PyJWT[crypto]>=2.8\""
        )
    key_path = Path(path)
    if not key_path.is_file():
        raise SigningKeyError(f"Signing key file not found: {key_path}")
    private_key = serialization.load_pem_private_key(
        key_path.read_bytes(), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise SigningKeyError("The signing key must be an RSA private key.")
    return _build_key(private_key)


def jwks_document(key: SigningKey) -> dict[str, Any]:
    """Return the JWKS document clients fetch to verify signatures."""
    return {"keys": [key.jwk()]}
