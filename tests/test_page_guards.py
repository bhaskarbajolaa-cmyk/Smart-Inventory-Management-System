"""Server-side page guards backed by the HttpOnly session cookie."""

from __future__ import annotations

import unittest
from uuid import uuid4

from inventory.app.main import build_application
from inventory.app.server import create_app


PUBLIC_PAGES = ("/login", "/signup", "/")
USER_PAGES = ("/home", "/home.html", "/workspace", "/workspace1", "/analytics", "/about")
ADMIN_PAGES = (
    "/admin",
    "/admin-roles",
    "/permissions",
    "/suppliers",
    "/cache-demo",
    "/cache-traffic",
)


class PageGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.services = build_application()
        cls.app = create_app(cls.services)

    # -- helpers ---------------------------------------------------------

    def client(self):
        return self.app.test_client()

    def login(self, client, username: str, password: str):
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def make_supervisor(self) -> tuple[str, str]:
        username = f"guard_{uuid4().hex[:8]}"
        password = "password123"
        self.services.authentication.register(
            username=username,
            email=f"{username}@example.com",
            password=password,
            role_id=3,
        )
        return username, password

    # -- public ----------------------------------------------------------

    def test_public_pages_stay_public(self) -> None:
        client = self.client()
        self.assertEqual(client.get("/login").status_code, 200)
        self.assertEqual(client.get("/signup").status_code, 200)
        redirect_home = client.get("/")
        self.assertEqual(redirect_home.status_code, 302)
        self.assertIn("/login", redirect_home.headers["Location"])

    # -- anonymous -------------------------------------------------------

    def test_anonymous_visitors_are_redirected_from_every_guarded_page(self) -> None:
        client = self.client()
        for path in USER_PAGES + ADMIN_PAGES:
            response = client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertTrue(
                response.headers["Location"].endswith("/login"), path
            )

    # -- signed-in non-admin ---------------------------------------------

    def test_signed_in_user_reaches_user_pages(self) -> None:
        username, password = self.make_supervisor()
        client = self.client()
        self.login(client, username, password)
        for path in USER_PAGES:
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_signed_in_user_is_forbidden_from_admin_pages(self) -> None:
        username, password = self.make_supervisor()
        client = self.client()
        self.login(client, username, password)
        for path in ADMIN_PAGES:
            self.assertEqual(client.get(path).status_code, 403, path)

    # -- administrator ---------------------------------------------------

    def test_admin_reaches_admin_pages(self) -> None:
        client = self.client()
        self.login(client, "admin", "admin1234")
        for path in ADMIN_PAGES:
            self.assertEqual(client.get(path).status_code, 200, path)

    # -- cookie behaviour ------------------------------------------------

    def test_login_sets_an_httponly_samesite_cookie(self) -> None:
        client = self.client()
        response = self.login(client, "admin", "admin1234")
        header = response.headers.get("Set-Cookie", "")
        self.assertIn("smobhar_session_token=", header)
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertIn("Path=/", header)

    def test_logout_clears_the_cookie_and_the_session(self) -> None:
        client = self.client()
        self.login(client, "admin", "admin1234")
        self.assertEqual(client.get("/home").status_code, 200)

        response = client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 204)
        self.assertIn("smobhar_session_token=;", response.headers.get("Set-Cookie", ""))
        self.assertEqual(client.get("/home").status_code, 302)

    def test_cookie_alone_does_not_authenticate_the_api(self) -> None:
        """The cookie is for page rendering only; APIs still need the Bearer token."""
        client = self.client()
        self.login(client, "admin", "admin1234")
        self.assertEqual(client.get("/home").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_forged_cookie_is_rejected(self) -> None:
        client = self.client()
        client.set_cookie("smobhar_session_token", "not-a-session")
        response = client.get("/home")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))


if __name__ == "__main__":
    unittest.main()
