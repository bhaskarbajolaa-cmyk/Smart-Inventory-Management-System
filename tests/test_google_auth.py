"""Offline tests for the Google authorization-code login service.

Nothing here touches the network: the token endpoint is a fake opener and the
ID token is signed with an ephemeral key whose public half is given to the
verifier as a JWKS document.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
import urllib.parse
import urllib.request

import pytest

from inventory.auth.google import GoogleTokenVerifier
from inventory.auth.google_auth import (
    GoogleLoginConfig,
    GoogleLoginError,
    GoogleLoginResult,
    GoogleLoginService,
)
from inventory.auth_server.keys import SigningKey, generate_signing_key, jwks_document
from inventory.auth_server.tokens import build_id_token

ISSUER = "http://127.0.0.1:5001"
CLIENT_ID = "smobhar-inventory"
REDIRECT_URI = "http://127.0.0.1:5000/api/auth/google/callback"
SUBJECT = "google-oauth2|1001"
EMAIL = "demo@smobhar.local"
ENVIRONMENT_VARIABLES = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_ISSUER",
    "GOOGLE_REDIRECT_URI",
    "GOOGLE_SCOPES",
    "GOOGLE_AUTO_PROVISION",
    "GOOGLE_DEFAULT_ROLE_ID",
)


# ----------------------------------------------------------------------
# Fixtures and doubles
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def signing_key() -> SigningKey:
    """One RSA key for the whole module; generating them is slow."""
    return generate_signing_key()


@pytest.fixture(scope="module")
def verifier(signing_key: SigningKey) -> GoogleTokenVerifier:
    return GoogleTokenVerifier(
        CLIENT_ID,
        issuers=(ISSUER,),
        jwks=jwks_document(signing_key),
    )


class FakeResponse:
    """The minimal slice of ``http.client.HTTPResponse`` we consume."""

    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self.closed = False
        self._body = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Record the token request and replay a canned response."""

    def __init__(self) -> None:
        self.payload: object = None
        self.status = 200
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float | None] = []

    def __call__(
        self, request: urllib.request.Request, timeout: float | None = None
    ) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        return FakeResponse(self.payload, status=self.status)

    @property
    def form(self) -> dict[str, list[str]]:
        assert self.requests, "the token endpoint was never called"
        body = self.requests[-1].data
        assert isinstance(body, bytes)
        return urllib.parse.parse_qs(body.decode("ascii"), keep_blank_values=True)


def make_config(**overrides: Any) -> GoogleLoginConfig:
    values: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "client_secret": None,
        "issuer": ISSUER,
        "redirect_uri": REDIRECT_URI,
        "authorize_endpoint": f"{ISSUER}/o/oauth2/v2/auth",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_url": f"{ISSUER}/oauth2/v3/certs",
    }
    values.update(overrides)
    return GoogleLoginConfig(**values)


def make_id_token(
    signing_key: SigningKey,
    nonce: str,
    *,
    audience: str = CLIENT_ID,
    issuer: str = ISSUER,
    lifetime: int = 300,
) -> str:
    issued_at = int(time.time())
    return build_id_token(
        key=signing_key,
        issuer=issuer,
        audience=audience,
        subject=SUBJECT,
        email=EMAIL,
        email_verified=True,
        name="Demo User",
        issued_at=issued_at,
        expires_at=issued_at + lifetime,
        nonce=nonce,
    )


def authorize_params(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


def begin(service: GoogleLoginService) -> tuple[str, str, dict[str, list[str]]]:
    url, state = service.start(return_to="/products")
    return url, state, authorize_params(url)


def unused_opener(request: urllib.request.Request, timeout: float | None = None) -> None:
    raise AssertionError("start() must not contact the provider")


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


def test_from_environment_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    config = GoogleLoginConfig.from_environment()

    assert config.client_id == "smobhar-inventory"
    assert config.client_secret is None
    assert config.issuer == "http://127.0.0.1:5001"
    assert config.redirect_uri == "http://127.0.0.1:5000/api/auth/google/callback"
    assert config.authorize_endpoint == "http://127.0.0.1:5001/o/oauth2/v2/auth"
    assert config.token_endpoint == "http://127.0.0.1:5001/token"
    assert config.jwks_url == "http://127.0.0.1:5001/oauth2/v3/certs"
    assert config.scopes == ("openid", "email", "profile")
    assert config.auto_provision is True
    assert config.default_role_id == 3


def test_from_environment_reads_overrides_and_strips_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "inventory-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "top-secret")
    monkeypatch.setenv("GOOGLE_ISSUER", "https://accounts.example.test/")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://inventory.example.test/callback")
    monkeypatch.setenv("GOOGLE_SCOPES", "openid email")
    monkeypatch.setenv("GOOGLE_AUTO_PROVISION", "0")
    monkeypatch.setenv("GOOGLE_DEFAULT_ROLE_ID", "7")

    config = GoogleLoginConfig.from_environment()

    assert config.client_id == "inventory-client"
    assert config.client_secret == "top-secret"
    assert config.issuer == "https://accounts.example.test"
    assert config.redirect_uri == "https://inventory.example.test/callback"
    assert config.authorize_endpoint == "https://accounts.example.test/o/oauth2/v2/auth"
    assert config.token_endpoint == "https://accounts.example.test/token"
    assert config.jwks_url == "https://accounts.example.test/oauth2/v3/certs"
    assert config.scopes == ("openid", "email")
    assert config.auto_provision is False
    assert config.default_role_id == 7


def test_empty_client_secret_means_public_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "   ")

    assert GoogleLoginConfig.from_environment().client_secret is None


# ----------------------------------------------------------------------
# start()
# ----------------------------------------------------------------------


def test_start_builds_authorize_url_with_pkce(verifier: GoogleTokenVerifier) -> None:
    service = GoogleLoginService(
        make_config(), verifier=verifier, opener=unused_opener, clock=lambda: 1000.0
    )

    url, state = service.start(return_to="/orders/42")

    parts = urllib.parse.urlsplit(url)
    params = authorize_params(url)
    assert parts.scheme == "http"
    assert parts.netloc == "127.0.0.1:5001"
    assert parts.path == "/o/oauth2/v2/auth"
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid email profile"]
    assert params["state"] == [state]
    assert params["code_challenge_method"] == ["S256"]
    assert params["access_type"] == ["online"]
    assert len(params["nonce"][0]) >= 32
    assert len(state) >= 32

    challenge = params["code_challenge"][0]
    assert len(challenge) == 43
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge
    assert "code_verifier" not in params
    assert "/orders/42" not in url


def test_code_challenge_is_s256_of_the_redeemed_verifier(
    verifier: GoogleTokenVerifier, signing_key: SigningKey
) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state, params = begin(service)
    opener.payload = {
        "id_token": make_id_token(signing_key, params["nonce"][0]),
        "access_token": "access-token-value",
    }

    service.complete(state, "auth-code")

    code_verifier = opener.form["code_verifier"][0]
    assert 43 <= len(code_verifier) <= 128
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert expected == params["code_challenge"][0]


# ----------------------------------------------------------------------
# complete() — happy path
# ----------------------------------------------------------------------


def test_complete_returns_verified_identity(
    verifier: GoogleTokenVerifier, signing_key: SigningKey
) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state, params = begin(service)
    id_token = make_id_token(signing_key, params["nonce"][0])
    opener.payload = {
        "id_token": id_token,
        "access_token": "access-token-value",
        "scope": "openid email profile",
        "expires_in": 3599,
        "token_type": "Bearer",
    }

    result = service.complete(state, "auth-code")

    assert isinstance(result, GoogleLoginResult)
    assert result.id_token == id_token
    assert result.access_token == "access-token-value"
    assert result.scope == "openid email profile"
    assert result.expires_in == 3599
    assert result.identity.subject == SUBJECT
    assert result.identity.email == EMAIL
    assert result.identity.email_verified is True
    assert result.identity.nonce == params["nonce"][0]
    assert service.config.issuer == ISSUER
    assert service.verifier is verifier

    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == f"{ISSUER}/token"
    assert request.method == "POST"
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert opener.timeouts == [5.0]

    form = opener.form
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["auth-code"]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["client_id"] == [CLIENT_ID]
    assert "client_secret" not in form


def test_client_secret_is_sent_when_configured(
    verifier: GoogleTokenVerifier, signing_key: SigningKey
) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(
        make_config(client_secret="client-secret-value"), verifier=verifier, opener=opener
    )
    url, state, params = begin(service)
    opener.payload = {"id_token": make_id_token(signing_key, params["nonce"][0])}

    result = service.complete(state, "auth-code")

    assert result.identity.email == EMAIL
    assert opener.form["client_secret"] == ["client-secret-value"]
    assert "client-secret-value" not in url


# ----------------------------------------------------------------------
# complete() — state handling
# ----------------------------------------------------------------------


def test_state_is_single_use(
    verifier: GoogleTokenVerifier, signing_key: SigningKey
) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state, params = begin(service)
    opener.payload = {"id_token": make_id_token(signing_key, params["nonce"][0])}

    service.complete(state, "auth-code")

    with pytest.raises(GoogleLoginError):
        service.complete(state, "auth-code")
    assert len(opener.requests) == 1


def test_unknown_state_is_rejected(verifier: GoogleTokenVerifier) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)

    with pytest.raises(GoogleLoginError, match="unknown or has expired"):
        service.complete("state-that-was-never-issued", "auth-code")

    assert opener.requests == []


def test_expired_state_is_rejected(verifier: GoogleTokenVerifier) -> None:
    now = [1000.0]
    opener = FakeOpener()
    service = GoogleLoginService(
        make_config(),
        verifier=verifier,
        opener=opener,
        state_ttl_seconds=300,
        clock=lambda: now[0],
    )
    url, state = service.start()

    now[0] += 301

    with pytest.raises(GoogleLoginError, match="expired"):
        service.complete(state, "auth-code")
    assert opener.requests == []


# ----------------------------------------------------------------------
# complete() — token verification and provider failures
# ----------------------------------------------------------------------


def test_nonce_mismatch_is_rejected(
    verifier: GoogleTokenVerifier, signing_key: SigningKey
) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state, params = begin(service)
    opener.payload = {"id_token": make_id_token(signing_key, "nonce-from-another-login")}

    with pytest.raises(GoogleLoginError, match="nonce"):
        service.complete(state, "auth-code")


def test_wrong_audience_is_rejected(
    verifier: GoogleTokenVerifier, signing_key: SigningKey
) -> None:
    opener = FakeOpener()
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state, params = begin(service)
    opener.payload = {
        "id_token": make_id_token(signing_key, params["nonce"][0], audience="another-client")
    }

    with pytest.raises(GoogleLoginError, match="could not be verified"):
        service.complete(state, "auth-code")


def test_provider_json_error_is_surfaced_and_redacted(verifier: GoogleTokenVerifier) -> None:
    opener = FakeOpener()
    opener.status = 400
    opener.payload = {
        "error": "invalid_grant",
        "error_description": "The code super-secret-code is invalid or expired.",
    }
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state = service.start()

    with pytest.raises(GoogleLoginError) as info:
        service.complete(state, "super-secret-code")

    message = str(info.value)
    assert "The provider rejected the login" in message
    assert "invalid_grant" in message
    assert "invalid or expired" in message
    assert "super-secret-code" not in message
    assert "[redacted]" in message


def test_provider_error_with_http_200_is_rejected(verifier: GoogleTokenVerifier) -> None:
    opener = FakeOpener()
    opener.payload = {"error": "invalid_request", "error_description": "Missing code."}
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state = service.start()

    with pytest.raises(GoogleLoginError, match="Missing code"):
        service.complete(state, "auth-code")


def test_non_200_response_is_rejected(verifier: GoogleTokenVerifier) -> None:
    opener = FakeOpener()
    opener.status = 500
    opener.payload = {"message": "internal error"}
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state = service.start()

    with pytest.raises(GoogleLoginError, match="HTTP 500"):
        service.complete(state, "auth-code")


def test_missing_id_token_is_rejected(verifier: GoogleTokenVerifier) -> None:
    opener = FakeOpener()
    opener.payload = {"access_token": "access-token-value", "scope": "openid"}
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state = service.start()

    with pytest.raises(GoogleLoginError, match="ID token"):
        service.complete(state, "auth-code")


def test_invalid_json_response_is_rejected(verifier: GoogleTokenVerifier) -> None:
    opener = FakeOpener()
    opener.payload = None
    service = GoogleLoginService(make_config(), verifier=verifier, opener=opener)
    url, state = service.start()

    with pytest.raises(GoogleLoginError, match="invalid response"):
        service.complete(state, "auth-code")


def test_unreachable_token_endpoint_is_rejected(verifier: GoogleTokenVerifier) -> None:
    def refusing_opener(request: urllib.request.Request, timeout: float | None = None):
        raise OSError("connection refused")

    service = GoogleLoginService(
        make_config(), verifier=verifier, opener=refusing_opener
    )
    url, state = service.start()

    with pytest.raises(GoogleLoginError, match="could not be reached"):
        service.complete(state, "auth-code")
