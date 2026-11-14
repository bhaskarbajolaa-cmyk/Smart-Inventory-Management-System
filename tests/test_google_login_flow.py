"""End-to-end Google sign-in: browser → provider → callback → session.

The provider runs on a real loopback socket (Werkzeug ``make_server``) and the
inventory app talks to it over HTTP, so the test covers the authorization
request, the consent step, the code exchange, JWKS retrieval, ID-token
verification and session adoption — not a mocked shortcut.
"""

from __future__ import annotations

import unittest
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace
from uuid import uuid4

from werkzeug.serving import make_server

from inventory.app.main import build_application
from inventory.app.server import create_app
from inventory.auth.google_auth import GoogleLoginConfig, GoogleLoginService
from inventory.auth_server.app import create_auth_server
from inventory.auth_server.config import AuthServerConfig, OAuthClient
from inventory.auth_server.keys import generate_signing_key
from inventory.auth_server.users import DemoUser, DemoUserDirectory


REDIRECT_URI = "http://127.0.0.1:5000/api/auth/google/callback"
LOCALHOST_REDIRECT_URI = "http://localhost:5000/api/auth/google/callback"
BASE_URL = "http://127.0.0.1:5000"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class GoogleLoginEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.email = f"e2e-{uuid4().hex[:10]}@smobhar.local"
        cls.client_id = f"inventory-e2e-{uuid4().hex[:6]}"
        provider_user = DemoUser(
            subject=f"google-oauth2|{uuid4().hex[:8]}",
            email=cls.email,
            name="E2E User",
        )

        # The issuer must contain the bound port, so bind first and then fix it.
        provider_config = AuthServerConfig(
            issuer="http://127.0.0.1:0",
            clients={
                cls.client_id: OAuthClient(
                    cls.client_id,
                    (REDIRECT_URI, LOCALHOST_REDIRECT_URI),
                    name="Inventory E2E",
                )
            },
            auto_approve=True,
        )
        provider_app = create_auth_server(
            provider_config,
            signing_key=generate_signing_key(),
            users=DemoUserDirectory([provider_user]),
        )
        cls.provider = make_server("127.0.0.1", 0, provider_app)
        cls.provider_url = f"http://127.0.0.1:{cls.provider.server_port}"
        provider_config.issuer = cls.provider_url
        cls.provider_thread = None
        import threading

        cls.provider_thread = threading.Thread(
            target=cls.provider.serve_forever, daemon=True
        )
        cls.provider_thread.start()

        cls.services = build_application()
        cls.services.google_login = GoogleLoginService(
            GoogleLoginConfig(
                client_id=cls.client_id,
                client_secret=None,
                issuer=cls.provider_url,
                redirect_uri=REDIRECT_URI,
                authorize_endpoint=f"{cls.provider_url}/o/oauth2/v2/auth",
                token_endpoint=f"{cls.provider_url}/token",
                jwks_url=f"{cls.provider_url}/oauth2/v3/certs",
            )
        )
        cls.app = create_app(cls.services)
        cls.client = cls.app.test_client()
        cls.opener = urllib.request.build_opener(_NoRedirect)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.provider.shutdown()
        user = cls.services.user_repository.find_by_email(cls.email)
        if user is not None:
            try:
                cls.services.user_repository.delete(user.user_id)
            except (KeyError, ValueError):
                pass

    # -- helpers ---------------------------------------------------------

    def _fetch(self, url: str, data: bytes | None = None):
        try:
            with self.opener.open(url, data=data, timeout=10) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def _authorize(self) -> tuple[str, dict[str, list[str]]]:
        """Run /start through the provider (consent included); return the code."""
        start = self.client.get("/api/auth/google/start", base_url=BASE_URL)
        self.assertEqual(start.status_code, 302)
        authorize_url = start.headers["Location"]
        self.assertTrue(authorize_url.startswith(self.provider_url))

        status, body, headers = self._fetch(authorize_url)
        if status == 200:
            # No login_hint was sent, so the provider rendered its consent page.
            params = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url).query)
            consent = {key: values[0] for key, values in params.items()}
            consent["email"] = self.email
            consent["decision"] = "allow"
            status, body, headers = self._fetch(
                f"{self.provider_url}/oauth2/auth/consent",
                data=urllib.parse.urlencode(consent).encode(),
            )
        self.assertEqual(status, 302)
        location = headers["Location"]
        self.assertTrue(location.startswith(REDIRECT_URI))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        self.assertNotIn("error", query)
        return query["code"][0], params_of(authorize_url)

    def _callback(self, code: str, state: str):
        return self.client.get(
            "/api/auth/google/callback?"
            + urllib.parse.urlencode({"code": code, "state": state}),
            base_url=BASE_URL,
        )

    # -- tests -----------------------------------------------------------

    def test_full_sign_in_provisions_a_view_only_account(self) -> None:
        code, params = self._authorize()
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertTrue(params["state"][0])

        callback = self._callback(code, params["state"][0])
        self.assertEqual(callback.status_code, 302)
        location = callback.headers["Location"]
        self.assertTrue(location.startswith("/login#"))
        fragment = urllib.parse.parse_qs(urllib.parse.urlparse(location).fragment)
        self.assertNotIn("error", fragment)
        token = fragment["session_token"][0]

        me = self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json["email"], self.email)
        self.assertEqual(me.json["username"], self.email.split("@")[0])
        self.assertEqual(me.json["role_id"], 3)
        self.assertEqual(me.json["permissions"], ["VIEW"])

        created = self.services.user_repository.find_by_email(self.email)
        self.assertIsNotNone(created)
        self.assertEqual(created.role_id, 3)

    def test_state_cannot_be_replayed(self) -> None:
        code, params = self._authorize()
        state = params["state"][0]
        self.assertEqual(self._callback(code, state).status_code, 302)

        replay = self._callback(code, state)
        self.assertEqual(replay.status_code, 302)
        fragment = urllib.parse.parse_qs(
            urllib.parse.urlparse(replay.headers["Location"]).fragment
        )
        self.assertIn("error", fragment)

    def test_unknown_state_is_rejected(self) -> None:
        response = self._callback("irrelevant-code", "forged-state")
        self.assertEqual(response.status_code, 302)
        fragment = urllib.parse.parse_qs(
            urllib.parse.urlparse(response.headers["Location"]).fragment
        )
        self.assertIn("error", fragment)

    def test_provider_error_parameter_is_reported(self) -> None:
        response = self.client.get("/api/auth/google/callback?error=access_denied")
        self.assertEqual(response.status_code, 302)
        fragment = urllib.parse.parse_qs(
            urllib.parse.urlparse(response.headers["Location"]).fragment
        )
        self.assertIn("error", fragment)

    def test_start_keeps_the_callback_on_the_request_host(self) -> None:
        """Opening the app on localhost must not hand back a 127.0.0.1 callback."""
        response = self.client.get(
            "/api/auth/google/start", base_url="http://localhost:5000"
        )
        self.assertEqual(response.status_code, 302)
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(response.headers["Location"]).query
        )
        self.assertEqual(query["redirect_uri"], [LOCALHOST_REDIRECT_URI])

    def test_start_returns_503_when_not_configured(self) -> None:
        client = create_app(SimpleNamespace()).test_client()
        response = client.get("/api/auth/google/start")
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json["description"])


def params_of(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


if __name__ == "__main__":
    unittest.main()
