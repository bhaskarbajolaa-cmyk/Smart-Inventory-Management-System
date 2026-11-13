"""Unit tests verifying Issue #14: Restricting role management to Administrators only."""

import unittest
from uuid import uuid4

from inventory.app.main import build_application
from inventory.app.server import create_app
from inventory.auth.models import User


class TestRoleManagementSecurity(unittest.TestCase):
    """Test that role creation, modification, and deletion are strictly Admin-only."""

    def setUp(self) -> None:
        self.services = build_application()
        self.app = create_app(self.services)
        self.client = self.app.test_client()

        # 1. Login as Admin
        admin_login = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.admin_token = admin_login.json["session_token"]
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}

        # 2. Create and login as a non-admin user (Supervisor, role_id=3)
        self.supervisor_username = f"sup_{uuid4().hex[:6]}"
        self.services.authentication.register(
            username=self.supervisor_username,
            email=f"{self.supervisor_username}@example.com",
            password="password123",
            role_id=3,  # Supervisor (read-only)
        )
        sup_login = self.client.post(
            "/api/auth/login", json={"username": self.supervisor_username, "password": "password123"}
        )
        self.supervisor_token = sup_login.json["session_token"]
        self.supervisor_headers = {"Authorization": f"Bearer {self.supervisor_token}"}

    def test_list_roles_requires_authentication(self) -> None:
        """GET /api/roles must reject unauthenticated requests with 401."""
        resp = self.client.get("/api/roles")
        self.assertEqual(resp.status_code, 401)

        # Authenticated user can read available roles
        resp = self.client.get("/api/roles", headers=self.supervisor_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json, list)
        role_names = [r["role_name"] for r in resp.json]
        self.assertIn("Admin", role_names)
        self.assertIn("Manager", role_names)
        self.assertIn("Supervisor", role_names)

    def test_create_role_permitted_for_admin(self) -> None:
        """POST /api/admin/roles must allow Administrator to create custom roles."""
        role_name = f"CustomRole_{uuid4().hex[:6]}"
        resp = self.client.post(
            "/api/admin/roles",
            headers=self.admin_headers,
            json={"role_name": role_name, "default_job_priority": 5},
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json
        self.assertEqual(data["role_name"], role_name)
        self.assertIn("role_id", data)

        # Clean up
        self.client.delete(f"/api/admin/roles/{data['role_id']}", headers=self.admin_headers)

    def test_create_role_forbidden_for_non_admin(self) -> None:
        """POST /api/admin/roles must return 403 Forbidden when called by non-admin."""
        resp = self.client.post(
            "/api/admin/roles",
            headers=self.supervisor_headers,
            json={"role_name": "UnauthorizedRole", "default_job_priority": 3},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Permission ADMIN is required", resp.json.get("description", ""))

    def test_delete_role_forbidden_for_non_admin(self) -> None:
        """DELETE /api/admin/roles/<id> must return 403 Forbidden for non-admin."""
        resp = self.client.delete(
            "/api/admin/roles/2",  # Attempt to delete Manager
            headers=self.supervisor_headers,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Permission ADMIN is required", resp.json.get("description", ""))

    def test_cannot_delete_system_default_roles(self) -> None:
        """Even an Admin cannot delete system default roles (Admin, Manager, Supervisor)."""
        # Role ID 1 is Admin
        resp = self.client.delete("/api/admin/roles/1", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("System default", resp.json.get("description", ""))

    def test_admin_can_delete_custom_role(self) -> None:
        """Admin can successfully delete a non-system role."""
        role_name = f"TempRole_{uuid4().hex[:6]}"
        create_resp = self.client.post(
            "/api/admin/roles",
            headers=self.admin_headers,
            json={"role_name": role_name},
        )
        role_id = create_resp.json["role_id"]

        del_resp = self.client.delete(f"/api/admin/roles/{role_id}", headers=self.admin_headers)
        self.assertEqual(del_resp.status_code, 204)

        # Verify it no longer exists
        all_roles = self.client.get("/api/roles", headers=self.admin_headers).json
        self.assertNotIn(role_id, [r["role_id"] for r in all_roles])
