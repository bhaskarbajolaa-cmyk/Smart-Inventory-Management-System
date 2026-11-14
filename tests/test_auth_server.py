"""Tests for the standalone OIDC provider and ``Session.google_token``.

These tests exercise the provider over its HTTP surface (Flask test client) and
the consumer side through ``Session.google_token``, so a regression in either
half — signature checks, PKCE, claim mapping, session lifetimes — fails here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from inventory.auth.google import (
    GoogleKeyError,
    GoogleTokenExpired,
    GoogleTokenInvalid,
    GoogleTokenVerifier,
)
from inventory.auth.models import Session, User
from inventory.auth.service import Authentication
from inventory.auth_server.app import create_auth_server
from inventory.auth_server.config import AuthServerConfig, OAuthClient
from inventory.auth_server.keys import generate_signing_key, jwks_document
from inventory.auth_server.tokens import build_id_token
from inventory.auth_server.users import DemoUserDirectory


ISSUER = "https://idp.smobhar.test"
CLIENT_ID = "smobhar-inventory"
REDIRECT_URI = "http://127.0.0.1:5000/api/auth/google/callback"
SECOND_REDIRECT_URI = "http://127.0.0.1:5000/api/auth/google/callback-alt"
EMAIL = "demo@smobhar.local"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = "smobhar-pkce-verifier-" + "x" * 32
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(location).query)


class OIDCProviderTestCase(unittest.TestCase):
    """Shared fixture: one provider, one public client, PKCE enforced."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.key = generate_signing_key()
        cls.jwks = jwks_document(cls.key)
        cls.users = DemoUserDirectory()
        cls.config = AuthServerConfig(
            issuer=ISSUER,
            clients={
                CLIENT_ID: OAuthClient(
                    CLIENT_ID,
                    (REDIRECT_URI, SECOND_REDIRECT_URI),
                    name="SMOभर Inventory",
                )
            },
            auto_approve=True,
        )
        cls.app = create_auth_server(cls.config, signing_key=cls.key, users=cls.users)
        cls.client = cls.app.test_client()

    # -- helpers ---------------------------------------------------------

    def authorize(
        self,
        *,
        client_id: str = CLIENT_ID,
        redirect_uri: str = REDIRECT_URI,
        scope: str = "openid email profile",
        extra: dict[str, str] | None = None,
        with_pkce: bool = True,
        login_hint: str | None = EMAIL,
    ):
        verifier, challenge = _pkce_pair()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": "state-123",
            "nonce": "nonce-abc",
        }
        if with_pkce:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        if login_hint:
            params["login_hint"] = login_hint
        params.update(extra or {})
        return self.client.get("/o/oauth2/v2/auth", query_string=params), verifier

    def exchange(self, code: str, verifier: str, **overrides):
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
        payload.update(overrides)
        return self.client.post("/token", data=payload)

    def issue_id_token(self, **overrides) -> str:
        now = int(time.time())
        values = {
            "key": self.key,
            "issuer": ISSUER,
            "audience": CLIENT_ID,
            "subject": "google-oauth2|1001",
            "email": EMAIL,
            "email_verified": True,
            "name": "Demo User",
            "issued_at": now,
            "expires_at": now + 600,
            "nonce": "nonce-abc",
        }
        values.update(overrides)
        return build_id_token(**values)


class DiscoveryAndMetadataTests(OIDCProviderTestCase):
    def test_discovery_document_describes_the_provider(self) -> None:
        response = self.client.get("/.well-known/openid-configuration")
        self.assertEqual(response.status_code, 200)
        document = response.json
        self.assertEqual(document["issuer"], ISSUER)
        self.assertEqual(document["authorization_endpoint"], f"{ISSUER}/o/oauth2/v2/auth")
        self.assertEqual(document["token_endpoint"], f"{ISSUER}/token")
        self.assertEqual(document["jwks_uri"], f"{ISSUER}/oauth2/v3/certs")
        self.assertEqual(document["id_token_signing_alg_values_supported"], ["RS256"])
        self.assertIn("S256", document["code_challenge_methods_supported"])
        self.assertIn("authorization_code", document["grant_types_supported"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_oauth_authorization_server_metadata_alias(self) -> None:
        response = self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["issuer"], ISSUER)

    def test_jwks_publishes_only_the_public_key(self) -> None:
        response = self.client.get("/oauth2/v3/certs")
        self.assertEqual(response.status_code, 200)
        keys = response.json["keys"]
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["kty"], "RSA")
        self.assertEqual(keys[0]["kid"], self.key.kid)
        self.assertNotIn("d", keys[0])
        self.assertNotIn("p", keys[0])

    def test_health_reports_issuer_and_key_id(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["kid"], self.key.kid)


class AuthorizationFlowTests(OIDCProviderTestCase):
    def test_authorization_code_flow_returns_a_verifiable_id_token(self) -> None:
        response, verifier = self.authorize()
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(location.startswith(REDIRECT_URI))
        query = _query(location)
        self.assertEqual(query["state"], ["state-123"])

        token_response = self.exchange(query["code"][0], verifier)
        self.assertEqual(token_response.status_code, 200)
        body = token_response.json
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["scope"], "openid email profile")
        self.assertIn("access_token", body)
        self.assertIn("id_token", body)
        self.assertNotIn("refresh_token", body)
        self.assertEqual(token_response.headers["Cache-Control"], "no-store")

        session = Session.google_token(
            body["id_token"], audience=CLIENT_ID, issuer=ISSUER, jwks=self.jwks
        )
        self.assertFalse(session.is_expired())
        identity = session.google_identity
        assert identity is not None
        self.assertEqual(identity.email, EMAIL)
        self.assertEqual(identity.subject, "google-oauth2|1001")
        self.assertEqual(identity.nonce, "nonce-abc")
        self.assertEqual(identity.issuer, ISSUER)
        self.assertEqual(identity.audience, (CLIENT_ID,))
        self.assertEqual(session.user_id, 0)
        self.assertEqual(session.roles, [])

    def test_id_token_audience_and_issuer_are_enforced(self) -> None:
        token = self.issue_id_token()
        Session.google_token(token, audience=CLIENT_ID, issuer=ISSUER, jwks=self.jwks)
        with self.assertRaises(GoogleTokenInvalid):
            Session.google_token(token, audience="another-client", issuer=ISSUER, jwks=self.jwks)
        with self.assertRaises(GoogleTokenInvalid):
            Session.google_token(
                token, audience=CLIENT_ID, issuer="https://evil.test", jwks=self.jwks
            )

    def test_unknown_client_never_redirects(self) -> None:
        response, _ = self.authorize(client_id="not-registered")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Unknown client", response.data)
        self.assertNotIn("Location", response.headers)

    def test_unregistered_redirect_uri_never_redirects(self) -> None:
        response, _ = self.authorize(redirect_uri="https://attacker.test/callback")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Invalid redirect_uri", response.data)
        self.assertNotIn("Location", response.headers)

    def test_wrong_redirect_uri_at_token_endpoint_is_rejected(self) -> None:
        response, verifier = self.authorize()
        code = _query(response.headers["Location"])["code"][0]
        token_response = self.exchange(code, verifier, redirect_uri=SECOND_REDIRECT_URI)
        self.assertEqual(token_response.status_code, 400)
        self.assertEqual(token_response.json["error"], "invalid_grant")

    def test_unsupported_response_type_redirects_with_error(self) -> None:
        response, _ = self.authorize(extra={"response_type": "token"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_query(response.headers["Location"])["error"], ["unsupported_response_type"])

    def test_unknown_scope_is_rejected(self) -> None:
        response, _ = self.authorize(scope="openid email admin")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_query(response.headers["Location"])["error"], ["invalid_scope"])

    def test_public_client_must_use_pkce(self) -> None:
        response, _ = self.authorize(with_pkce=False)
        self.assertEqual(response.status_code, 302)
        query = _query(response.headers["Location"])
        self.assertEqual(query["error"], ["invalid_request"])
        self.assertIn("PKCE", query["error_description"][0])

    def test_prompt_none_without_session_returns_login_required(self) -> None:
        response, _ = self.authorize(login_hint=None, extra={"prompt": "none"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_query(response.headers["Location"])["error"], ["login_required"])

    def test_prompt_none_with_login_hint_issues_a_code(self) -> None:
        response, _ = self.authorize(extra={"prompt": "none"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("code", _query(response.headers["Location"]))

    def test_consent_screen_and_denial(self) -> None:
        config = AuthServerConfig(
            issuer=ISSUER,
            clients={CLIENT_ID: self.config.clients[CLIENT_ID]},
            auto_approve=False,
        )
        client = create_auth_server(config, signing_key=self.key, users=self.users).test_client()
        verifier, challenge = _pkce_pair()
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email",
            "state": "s-consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        page = client.get("/o/oauth2/v2/auth", query_string=params)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Choose an account", page.data)

        denied = client.post(
            "/oauth2/auth/consent",
            data={**params, "decision": "deny", "email": EMAIL},
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(_query(denied.headers["Location"])["error"], ["access_denied"])

        allowed = client.post(
            "/oauth2/auth/consent",
            data={**params, "decision": "allow", "email": EMAIL},
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertIn("code", _query(allowed.headers["Location"]))


class TokenEndpointTests(OIDCProviderTestCase):
    def _code(self) -> tuple[str, str]:
        response, verifier = self.authorize()
        return _query(response.headers["Location"])["code"][0], verifier

    def test_pkce_verifier_mismatch_is_rejected(self) -> None:
        code, _verifier = self._code()
        response = self.exchange(code, "wrong-verifier")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "invalid_grant")
        self.assertIn("PKCE", response.json["error_description"])

    def test_missing_pkce_verifier_is_rejected(self) -> None:
        code, _verifier = self._code()
        response = self.exchange(code, "")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "invalid_grant")

    def test_authorization_code_is_single_use(self) -> None:
        code, verifier = self._code()
        self.assertEqual(self.exchange(code, verifier).status_code, 200)
        replay = self.exchange(code, verifier)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json["error"], "invalid_grant")

    def test_unknown_code_is_rejected(self) -> None:
        response = self.exchange("not-a-code", "verifier")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "invalid_grant")

    def test_unsupported_grant_type(self) -> None:
        response = self.client.post(
            "/token", data={"grant_type": "password", "client_id": CLIENT_ID}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "unsupported_grant_type")

    def test_refresh_token_flow(self) -> None:
        response, verifier = self.authorize(extra={"access_type": "offline"})
        code = _query(response.headers["Location"])["code"][0]
        first = self.exchange(code, verifier)
        self.assertEqual(first.status_code, 200)
        refresh_token = first.json["refresh_token"]

        refreshed = self.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
        self.assertEqual(refreshed.status_code, 200)
        self.assertIn("access_token", refreshed.json)
        self.assertIn("id_token", refreshed.json)
        self.assertEqual(refreshed.json["refresh_token"], refresh_token)

    def test_refresh_token_for_another_client_is_rejected(self) -> None:
        response, verifier = self.authorize(extra={"access_type": "offline"})
        code = _query(response.headers["Location"])["code"][0]
        refresh_token = self.exchange(code, verifier).json["refresh_token"]
        response = self.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": "another-client",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "invalid_client")

    def test_confidential_client_uses_basic_authentication(self) -> None:
        secret = "s3cret-value"
        confidential = OAuthClient(
            "confidential-client", (REDIRECT_URI,), client_secret=secret
        )
        config = AuthServerConfig(
            issuer=ISSUER,
            clients={confidential.client_id: confidential},
            auto_approve=True,
        )
        client = create_auth_server(config, signing_key=self.key, users=self.users).test_client()
        verifier, challenge = _pkce_pair()
        response = client.get(
            "/o/oauth2/v2/auth",
            query_string={
                "client_id": confidential.client_id,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": "openid email",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "login_hint": EMAIL,
            },
        )
        code = _query(response.headers["Location"])["code"][0]
        credentials = base64.b64encode(f"{confidential.client_id}:{secret}".encode()).decode()

        bad = client.post(
            "/token",
            headers={"Authorization": f"Basic {credentials[:-2]}xx"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json["error"], "invalid_client")
        self.assertIn("Basic", bad.headers["WWW-Authenticate"])

        good = client.post(
            "/token",
            headers={"Authorization": f"Basic {credentials}"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        self.assertEqual(good.status_code, 200)
        self.assertIn("id_token", good.json)


class UserinfoTests(OIDCProviderTestCase):
    def _access_token(self) -> str:
        response, verifier = self.authorize()
        code = _query(response.headers["Location"])["code"][0]
        return self.exchange(code, verifier).json["access_token"]

    def test_userinfo_requires_a_bearer_token(self) -> None:
        response = self.client.get("/oauth2/v2/userinfo")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers["WWW-Authenticate"])

    def test_userinfo_rejects_a_garbage_token(self) -> None:
        response = self.client.get(
            "/oauth2/v2/userinfo", headers={"Authorization": "Bearer not-a-token"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "invalid_token")

    def test_userinfo_rejects_an_id_token(self) -> None:
        response, verifier = self.authorize()
        code = _query(response.headers["Location"])["code"][0]
        id_token = self.exchange(code, verifier).json["id_token"]
        rejected = self.client.get(
            "/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {id_token}"}
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.json["error"], "invalid_token")

    def test_userinfo_returns_google_shaped_claims(self) -> None:
        response = self.client.get(
            "/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {self._access_token()}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["email"], EMAIL)
        self.assertTrue(response.json["email_verified"])
        self.assertEqual(response.json["sub"], "google-oauth2|1001")


class GoogleTokenMappingTests(OIDCProviderTestCase):
    def test_resolver_maps_identity_to_local_user_and_roles(self) -> None:
        user = User(
            user_id=7,
            username="demo",
            email=EMAIL,
            role_id=2,
            created_at=datetime.now(),
        )
        token = self.issue_id_token()
        session = Session.google_token(
            token,
            audience=CLIENT_ID,
            issuer=ISSUER,
            jwks=self.jwks,
            user_resolver=lambda identity: user,
        )
        self.assertEqual(session.user_id, 7)
        self.assertEqual(session.roles, ["2"])
        self.assertEqual(session.password, "")

    def test_unlinked_identity_raises_instead_of_creating_a_session(self) -> None:
        token = self.issue_id_token()
        with self.assertRaises(LookupError):
            Session.google_token(
                token,
                audience=CLIENT_ID,
                issuer=ISSUER,
                jwks=self.jwks,
                user_resolver=lambda identity: None,
            )

    def test_session_lifetime_is_capped_by_the_token(self) -> None:
        token = self.issue_id_token(expires_at=int(time.time()) + 120)
        session = Session.google_token(
            token,
            audience=CLIENT_ID,
            issuer=ISSUER,
            jwks=self.jwks,
            lifetime=timedelta(days=30),
        )
        identity = session.google_identity
        assert identity is not None
        self.assertLessEqual(session.expires_at, identity.expires_at)
        self.assertLess(session.expires_at - session.issued_at, timedelta(minutes=5))

    def test_missing_audience_is_a_configuration_error(self) -> None:
        with self.assertRaises(ValueError):
            Session.google_token(self.issue_id_token(), jwks=self.jwks)

    def test_non_positive_lifetime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Session.google_token(
                self.issue_id_token(),
                audience=CLIENT_ID,
                issuer=ISSUER,
                jwks=self.jwks,
                lifetime=timedelta(0),
            )

    def test_expired_token_is_rejected(self) -> None:
        now = int(time.time())
        token = self.issue_id_token(issued_at=now - 7200, expires_at=now - 3600)
        with self.assertRaises(GoogleTokenExpired):
            Session.google_token(token, audience=CLIENT_ID, issuer=ISSUER, jwks=self.jwks)

    def test_unsigned_algorithm_is_rejected(self) -> None:
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps({"sub": "x", "aud": CLIENT_ID}).encode())
        with self.assertRaises(GoogleTokenInvalid):
            Session.google_token(
                f"{header}.{payload}.", audience=CLIENT_ID, issuer=ISSUER, jwks=self.jwks
            )

    def test_hmac_algorithm_confusion_is_rejected(self) -> None:
        payload = _b64url(
            json.dumps(
                {
                    "iss": ISSUER,
                    "aud": CLIENT_ID,
                    "sub": "x",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 600,
                }
            ).encode()
        )
        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": self.key.kid}).encode())
        with self.assertRaises(GoogleTokenInvalid):
            Session.google_token(
                f"{header}.{payload}.signature",
                audience=CLIENT_ID,
                issuer=ISSUER,
                jwks=self.jwks,
            )

    def test_tampered_signature_is_rejected(self) -> None:
        token = self.issue_id_token()
        header, payload, _signature = token.split(".")
        forged = f"{header}.{payload}.{_b64url(bytes(64))}"
        with self.assertRaises(GoogleTokenInvalid):
            Session.google_token(forged, audience=CLIENT_ID, issuer=ISSUER, jwks=self.jwks)

    def test_unverified_email_is_rejected_by_default(self) -> None:
        token = self.issue_id_token(email_verified=False)
        with self.assertRaises(GoogleTokenInvalid):
            Session.google_token(token, audience=CLIENT_ID, issuer=ISSUER, jwks=self.jwks)

    def test_unverified_email_can_be_allowed_explicitly(self) -> None:
        token = self.issue_id_token(email_verified=False)
        session = Session.google_token(
            token,
            audience=CLIENT_ID,
            issuer=ISSUER,
            jwks=self.jwks,
            require_email_verified=False,
        )
        identity = session.google_identity
        assert identity is not None
        self.assertFalse(identity.email_verified)

    def test_unknown_key_id_is_rejected(self) -> None:
        other_key = generate_signing_key()
        now = int(time.time())
        foreign = build_id_token(
            key=other_key,
            issuer=ISSUER,
            audience=CLIENT_ID,
            subject="google-oauth2|1001",
            email=EMAIL,
            email_verified=True,
            issued_at=now,
            expires_at=now + 600,
        )
        with self.assertRaises(GoogleKeyError):
            Session.google_token(
                foreign,
                audience=CLIENT_ID,
                issuer=ISSUER,
                jwks=self.jwks,
                jwks_url=None,
            )


class SessionAdoptionTests(unittest.TestCase):
    """A Google-authenticated session must be usable by the HTTP API."""

    def _session(self) -> Session:
        now = datetime.now(timezone.utc)
        return Session(
            session_token="google-session-token",
            user_id=1,
            roles=["1"],
            issued_at=now,
            expires_at=now + timedelta(hours=1),
        )

    def test_adopt_session_is_resolvable_by_get_session(self) -> None:
        authentication = Authentication(user_repository=object())
        session = authentication.adopt_session(self._session())
        self.assertIs(authentication.get_session(session.session_token), session)

    def test_adopt_rejects_expired_sessions(self) -> None:
        authentication = Authentication(user_repository=object())
        expired = self._session()
        expired.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        with self.assertRaises(ValueError):
            authentication.adopt_session(expired)

    def test_adopt_rejects_empty_tokens(self) -> None:
        authentication = Authentication(user_repository=object())
        session = self._session()
        session.session_token = ""
        with self.assertRaises(ValueError):
            authentication.adopt_session(session)


class VerifierUnitTests(unittest.TestCase):
    def test_verifier_requires_an_audience(self) -> None:
        with self.assertRaises(ValueError):
            GoogleTokenVerifier("")

    def test_verifier_rejects_empty_tokens(self) -> None:
        key = generate_signing_key()
        verifier = GoogleTokenVerifier(CLIENT_ID, jwks=jwks_document(key))
        with self.assertRaises(GoogleTokenInvalid):
            verifier.verify("")

    def test_verifier_rejects_jwks_without_usable_keys(self) -> None:
        with self.assertRaises(GoogleKeyError):
            GoogleTokenVerifier(CLIENT_ID, jwks={"keys": []})

    def test_email_claim_is_required_when_verification_is_on(self) -> None:
        key = generate_signing_key()
        now = int(time.time())
        token = build_id_token(
            key=key,
            issuer=ISSUER,
            audience=CLIENT_ID,
            subject="sub-no-email",
            email="",
            email_verified=True,
            issued_at=now,
            expires_at=now + 600,
        )
        with self.assertRaises(GoogleTokenInvalid):
            GoogleTokenVerifier(
                CLIENT_ID, issuers=ISSUER, jwks=jwks_document(key)
            ).verify(token)


if __name__ == "__main__":
    unittest.main()
