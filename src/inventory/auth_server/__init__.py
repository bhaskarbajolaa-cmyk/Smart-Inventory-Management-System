"""Standalone OAuth 2.0 / OpenID Connect provider (Google-compatible)."""

from inventory.auth_server.app import create_auth_server
from inventory.auth_server.config import AuthServerConfig, OAuthClient
from inventory.auth_server.keys import SigningKey, generate_signing_key, load_signing_key
from inventory.auth_server.users import DemoUser, DemoUserDirectory

__all__ = [
    "AuthServerConfig",
    "DemoUser",
    "DemoUserDirectory",
    "OAuthClient",
    "SigningKey",
    "create_auth_server",
    "generate_signing_key",
    "load_signing_key",
]
