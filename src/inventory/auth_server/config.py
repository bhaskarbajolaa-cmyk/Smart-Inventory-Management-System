"""Configuration for the standalone OAuth 2.0 / OpenID Connect provider.

The provider is intentionally self-contained: it owns its clients, its demo
accounts and its signing key. Nothing here imports the inventory database, so
the same service could be pointed at a real Google tenant by changing the
issuer, the client registration and the signing key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os


DEFAULT_ISSUER = "http://127.0.0.1:5001"
DEFAULT_CLIENT_ID = "smobhar-inventory"
DEFAULT_REDIRECT_URIS = (
    "http://127.0.0.1:5000/api/auth/google/callback",
    "http://localhost:5000/api/auth/google/callback",
)
DEFAULT_SCOPE = "openid email profile"

SUPPORTED_SCOPES = frozenset({"openid", "email", "profile"})
SUPPORTED_RESPONSE_TYPES = ("code",)
SUPPORTED_GRANT_TYPES = ("authorization_code", "refresh_token")
SUPPORTED_CODE_CHALLENGE_METHODS = ("S256", "plain")


class AuthServerConfigError(ValueError):
    """Raised when the provider is configured with unusable values."""


@dataclass(frozen=True)
class OAuthClient:
    """A registered OAuth client.

    A client without a secret is public and must use PKCE. A client with a
    secret is confidential and authenticates at the token endpoint.
    """

    client_id: str
    redirect_uris: tuple[str, ...]
    client_secret: str | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if not self.client_id:
            raise AuthServerConfigError("Every OAuth client needs a client_id.")
        if not self.redirect_uris:
            raise AuthServerConfigError(
                f"Client '{self.client_id}' needs at least one redirect URI."
            )
        for uri in self.redirect_uris:
            if not isinstance(uri, str) or not uri.startswith(("http://", "https://")):
                raise AuthServerConfigError(
                    f"Client '{self.client_id}' has an invalid redirect URI: {uri!r}."
                )

    @property
    def is_public(self) -> bool:
        return not self.client_secret

    def allows_redirect_uri(self, uri: str | None) -> bool:
        """Require an exact match, as RFC 6749 section 3.1.2.3 mandates."""
        return bool(uri) and uri in self.redirect_uris


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise AuthServerConfigError(f"{name} must be an integer.") from error


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class AuthServerConfig:
    """Runtime settings for the identity provider."""

    issuer: str = DEFAULT_ISSUER
    clients: dict[str, OAuthClient] = field(default_factory=dict)
    id_token_ttl_seconds: int = 3600
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 86400
    authorization_code_ttl_seconds: int = 300
    auto_approve: bool = True
    require_pkce_for_public_clients: bool = True
    default_scope: str = DEFAULT_SCOPE

    def __post_init__(self) -> None:
        self.issuer = self.issuer.rstrip("/")
        if not self.issuer.startswith(("http://", "https://")):
            raise AuthServerConfigError("issuer must be an http(s) URL.")
        for name in (
            "id_token_ttl_seconds",
            "access_token_ttl_seconds",
            "refresh_token_ttl_seconds",
            "authorization_code_ttl_seconds",
        ):
            if getattr(self, name) <= 0:
                raise AuthServerConfigError(f"{name} must be positive.")
        if not self.clients:
            raise AuthServerConfigError("At least one OAuth client must be registered.")

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.issuer}/o/oauth2/v2/auth"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/token"

    @property
    def userinfo_endpoint(self) -> str:
        return f"{self.issuer}/oauth2/v2/userinfo"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/oauth2/v3/certs"

    @classmethod
    def from_environment(cls) -> "AuthServerConfig":
        """Build a configuration from ``AUTH_SERVER_*`` environment values."""
        client_id = os.environ.get("AUTH_SERVER_CLIENT_ID", "").strip() or DEFAULT_CLIENT_ID
        client_secret = os.environ.get("AUTH_SERVER_CLIENT_SECRET", "").strip() or None
        redirects = tuple(
            uri.strip()
            for uri in os.environ.get("AUTH_SERVER_REDIRECT_URI", "").split(",")
            if uri.strip()
        ) or DEFAULT_REDIRECT_URIS
        client = OAuthClient(
            client_id=client_id,
            redirect_uris=redirects,
            client_secret=client_secret,
            name="SMOभर Inventory",
        )
        return cls(
            issuer=os.environ.get("AUTH_SERVER_ISSUER", "").strip() or DEFAULT_ISSUER,
            clients={client.client_id: client},
            id_token_ttl_seconds=_env_int("AUTH_SERVER_ID_TOKEN_TTL", 3600),
            access_token_ttl_seconds=_env_int("AUTH_SERVER_ACCESS_TOKEN_TTL", 3600),
            refresh_token_ttl_seconds=_env_int("AUTH_SERVER_REFRESH_TOKEN_TTL", 86400),
            authorization_code_ttl_seconds=_env_int("AUTH_SERVER_CODE_TTL", 300),
            auto_approve=_env_bool("AUTH_SERVER_AUTO_APPROVE", True),
            require_pkce_for_public_clients=_env_bool(
                "AUTH_SERVER_REQUIRE_PKCE", True
            ),
        )
