"""Optional Flask backend API for the inventory application."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import threading
from collections import deque
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import time
import urllib.parse
from typing import Any

from inventory.access_control.models import Permission, Role, TableAccessGrant
from inventory.app.services import ApplicationServices
from inventory.app.workflows import submit_sale_job
from inventory.auth.models import Session, User
from inventory.catalog.filters import ProductFilter


EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class RequestMetrics:
    """Track dynamic application requests for the current server process."""

    WINDOW_SECONDS = 60
    BUCKET_SECONDS = 5

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._requests: deque[float] = deque()

    def record(self) -> None:
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        with self._lock:
            self._total += 1
            self._requests.append(now)
            while self._requests and self._requests[0] < cutoff:
                self._requests.popleft()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        bucket_count = self.WINDOW_SECONDS // self.BUCKET_SECONDS
        buckets = [0] * bucket_count
        with self._lock:
            while self._requests and self._requests[0] < cutoff:
                self._requests.popleft()
            total = self._total
            recent = tuple(self._requests)

        for timestamp in recent:
            bucket = min(
                bucket_count - 1,
                int((timestamp - cutoff) // self.BUCKET_SECONDS),
            )
            buckets[max(0, bucket)] += 1

        return {
            "total_requests": total,
            "recent_requests": len(recent),
            "window_seconds": self.WINDOW_SECONDS,
            "bucket_seconds": self.BUCKET_SECONDS,
            "buckets": buckets,
        }


def create_app(services: ApplicationServices | None = None) -> Any:
    """Create an HTTP app around an already-constructed service graph."""
    if services is None:
        from inventory.app.main import build_application
        services = build_application()

    import os
    from flask import Flask, abort, jsonify, make_response, redirect, request

    # Resolve templates/ and static/ relative to this file (src/inventory/app/)
    _here = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(_here, "templates"),
        static_folder=os.path.join(_here, "static"),
        static_url_path="/static",
    )
    request_metrics = RequestMetrics()
    app.extensions["request_metrics"] = request_metrics
    app.extensions["services"] = services
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
        "SESSION_COOKIE_SECURE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}

    @app.before_request
    def count_application_request() -> None:
        if (
            request.path != "/health"
            and request.path != "/api/cache/traffic"
            and not request.path.startswith("/static/")
        ):
            request_metrics.record()

    # Register frontend page routes
    from inventory.app.web import SESSION_COOKIE, register_page_routes
    register_page_routes(app)

    from werkzeug.exceptions import HTTPException

    @app.errorhandler(HTTPException)
    def api_http_error(error: HTTPException) -> Any:
        """Return JSON bodies for /api/ errors so the frontend can read `description`."""
        if request.path.startswith("/api/"):
            return jsonify({"error": error.name, "description": error.description}), error.code
        return error.get_response(request)

    @app.errorhandler(Exception)
    def api_unexpected_error(error: Exception) -> Any:
        """Convert unhandled exceptions on /api/ into JSON 500 responses."""
        if isinstance(error, HTTPException) or not request.path.startswith("/api/"):
            return error
        return jsonify({"error": "Internal Server Error", "description": str(error)}), 500

    from flask import abort, jsonify, request

    def require_session() -> Session:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            abort(401, description="Provide a valid Bearer session token.")

        session = services.authentication.get_session(token)
        if session is None or session.is_expired():
            abort(401, description="Session is missing or expired.")
        return session

    def require_permission(code: str, table_name: str) -> tuple[User, Role]:
        session = require_session()
        user = services.user_repository.find_by_id(session.user_id)
        if user is None:
            abort(401, description="The session user no longer exists.")

        role = services.role_repository.find_by_id(user.role_id)
        if role is None:
            abort(403, description="The user's role no longer exists.")
        if not user.has_permission(code, role):
            abort(403, description=f"Permission {code} is required for {table_name}.")
        return user, role

    def set_session_cookie(response: Any, token: str) -> Any:
        """Attach the HttpOnly session cookie used by the server-side page guards.

        The cookie is never accepted as API authentication: API routes still
        require the Bearer header, so a cross-site request cannot ride on it.
        """
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="Lax",
            secure=bool(app.config.get("SESSION_COOKIE_SECURE", False)),
            path="/",
        )
        return response

    def product_payload(product: Any) -> dict[str, Any]:
        return {
            "product_id": product.product_id,
            "name": product.name,
            "sku": product.sku,
            "category": product.category,
            "quantity": product.quantity,
            "reorder_level": product.reorder_level,
            "unit_price": str(product.unit_price),
            "supplier_id": product.supplier_id,
            "updated_at": product.updated_at.isoformat(),
        }

    PRODUCT_WRITE_FIELDS = ("name", "sku", "category", "quantity", "reorder_level", "unit_price", "supplier_id")

    def sku_taken(sku: str, exclude_product_id: int | None = None) -> bool:
        """Return True when another product row already owns the given SKU."""
        sql = "SELECT product_id FROM products WHERE sku = ?"
        params: list[Any] = [sku]
        if exclude_product_id is not None:
            sql += " AND product_id <> ?"
            params.append(exclude_product_id)
        return bool(services.database_manager.execute_query(sql, params))

    def product_from_payload(payload: dict[str, Any], product_id: int) -> Any:
        """Validate a product request body and map it onto the Product model."""
        from inventory.catalog.models import Product

        missing = [
            field
            for field in PRODUCT_WRITE_FIELDS
            if payload.get(field) is None or payload.get(field) == ""
        ]
        if missing:
            abort(400, description="Missing or empty required field(s): " + ", ".join(missing) + ".")

        def as_int(field: str) -> int:
            try:
                return int(payload[field])
            except (TypeError, ValueError):
                abort(400, description=f"{field} must be an integer.")

        try:
            unit_price = Decimal(str(payload["unit_price"]))
        except InvalidOperation:
            abort(400, description="unit_price must be a decimal number.")

        supplier_id = as_int("supplier_id")
        if services.supplier_repository.find_by_id(supplier_id) is None:
            abort(400, description=f"Supplier ID {supplier_id} does not exist. Choose one of the listed suppliers.")

        try:
            return Product(
                product_id=product_id,
                name=str(payload["name"]).strip(),
                sku=str(payload["sku"]).strip(),
                category=str(payload["category"]).strip(),
                quantity=as_int("quantity"),
                reorder_level=as_int("reorder_level"),
                unit_price=unit_price,
                supplier_id=supplier_id,
                updated_at=datetime.now(),
            )
        except ValueError as error:
            abort(400, description=str(error))

    @app.get("/api/users")
    def list_users() -> Any:
        require_permission("ADMIN", "users")
        users = services.user_repository.find_all()
        return jsonify([
            {
                "user_id": u.user_id,
                "username": u.username,
                "email": u.email,
                "role_id": u.role_id,
                "created_at": u.created_at.isoformat() if hasattr(u.created_at, "isoformat") else str(u.created_at),
            }
            for u in users
        ])

    @app.post("/api/admin/users")
    def create_admin_user() -> Any:
        require_permission("ADMIN", "users")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        username = payload.get("username")
        email = payload.get("email", "")
        password = payload.get("password")
        role_id = payload.get("role_id")

        if not isinstance(username, str) or not username.strip():
            abort(400, description="username must be a non-empty string.")
        if not isinstance(password, str) or len(password) < 8:
            abort(400, description="password must be at least 8 characters.")
        if not isinstance(email, str) or not EMAIL_PATTERN.fullmatch(email.strip()):
            abort(400, description="email must be a valid email address.")
        try:
            role_id = int(role_id)
        except (TypeError, ValueError):
            abort(400, description="role_id must be an integer.")
        if services.role_repository.find_by_id(role_id) is None:
            abort(400, description=f"role_id {role_id} does not match an existing role.")

        try:
            user = services.authentication.register(
                username=username.strip(),
                email=email.strip(),
                password=password,
                role_id=role_id,
            )
        except ValueError as exc:
            abort(409, description=str(exc))

        return jsonify(
            {"user_id": user.user_id, "username": user.username, "email": user.email, "role_id": user.role_id}
        ), 201

    @app.put("/api/admin/users/<int:user_id>")
    def update_admin_user(user_id: int) -> Any:
        """Update a user's username, email or role. Only supplied fields change."""
        admin, _role = require_permission("ADMIN", "users")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        user = services.user_repository.find_by_id(user_id)
        if user is None:
            abort(404, description="User not found.")

        unknown = set(payload) - {"username", "email", "role_id"}
        if unknown:
            abort(400, description=f"Unsupported field(s): {', '.join(sorted(unknown))}.")
        if not payload:
            abort(400, description="Provide at least one field to update.")

        if "username" in payload:
            username = payload["username"]
            if not isinstance(username, str) or not username.strip():
                abort(400, description="username must be a non-empty string.")
            clash = services.user_repository.find_by_username(username.strip())
            if clash is not None and clash.user_id != user_id:
                abort(409, description=f"Username '{username.strip()}' is already taken.")
            user.username = username.strip()

        if "email" in payload:
            email = payload["email"]
            if not isinstance(email, str) or not EMAIL_PATTERN.fullmatch(email.strip()):
                abort(400, description="email must be a valid email address.")
            user.email = email.strip()

        if "role_id" in payload:
            try:
                role_id = int(payload["role_id"])
            except (TypeError, ValueError):
                abort(400, description="role_id must be an integer.")
            if services.role_repository.find_by_id(role_id) is None:
                abort(400, description=f"role_id {role_id} does not match an existing role.")
            if user.user_id == admin.user_id and role_id != admin.role_id:
                abort(403, description="You cannot change your own role.")
            user.role_id = role_id

        updated = services.user_repository.save(user)
        return jsonify(
            {
                "user_id": updated.user_id,
                "username": updated.username,
                "email": updated.email,
                "role_id": updated.role_id,
            }
        )

    @app.delete("/api/admin/users/<int:user_id>")
    def delete_admin_user(user_id: int) -> Any:
        user, _role = require_permission("ADMIN", "users")
        if user.user_id == user_id:
            abort(403, description="You cannot delete yourself.")

        # stock_transactions.user_id has no ON DELETE action, so a user with an
        # audit trail cannot be removed without orphaning those rows.
        rows = services.database_manager.execute_query(
            "SELECT COUNT(*) AS count FROM stock_transactions WHERE user_id = ?", (user_id,)
        )
        if rows and int(rows[0]["count"]) > 0:
            abort(409, description="This user has stock transactions on record and cannot be deleted.")

        try:
            services.user_repository.delete(user_id)
        except KeyError:
            abort(404, description="User not found.")
        return "", 204

    @app.get("/api/roles")
    def list_roles() -> Any:
        """List all available roles and their permission grants."""
        require_session()
        roles = services.role_repository.find_all()

        grant_rows = services.database_manager.execute_query(
            """
            SELECT g.role_id, p.code
            FROM table_access_grants g
            JOIN permissions p ON p.permission_id = g.permission_id
            ORDER BY p.permission_id ASC
            """
        )
        grants_by_role: dict[int, set[str]] = {}
        for row in grant_rows:
            grants_by_role.setdefault(int(row["role_id"]), set()).add(row["code"])

        user_rows = services.database_manager.execute_query("SELECT user_id, username FROM users")
        usernames = {int(row["user_id"]): row["username"] for row in user_rows}

        return jsonify([
            {
                "role_id": r.role_id,
                "role_name": r.role_name,
                "created_by": r.created_by,
                "created_by_username": usernames.get(int(r.created_by), ""),
                "is_system_default": r.is_system_default,
                "default_job_priority": r.default_job_priority,
                "permissions": sorted(grants_by_role.get(int(r.role_id), set())),
            }
            for r in roles
        ])

    @app.post("/api/admin/roles")
    def create_role() -> Any:
        """Create a new role (Administrator only)."""
        user, _role = require_permission("ADMIN", "roles")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        role_name = payload.get("role_name")
        if not isinstance(role_name, str) or not role_name.strip():
            abort(400, description="role_name must be a non-empty string.")

        priority = payload.get("default_job_priority", 3)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            abort(400, description="default_job_priority must be an integer.")

        created_by = payload.get("created_by", user.user_id)
        try:
            created_by = int(created_by)
        except (TypeError, ValueError):
            abort(400, description="created_by must be an integer user id.")
        if created_by != 0 and services.user_repository.find_by_id(created_by) is None:
            abort(400, description=f"created_by {created_by} does not match an existing user.")

        is_system_default = payload.get("is_system_default", False)
        if isinstance(is_system_default, str):
            is_system_default = is_system_default.strip().lower() in {"true", "1", "yes"}
        is_system_default = bool(is_system_default)

        raw_permissions = payload.get("permissions", [])
        if isinstance(raw_permissions, str):
            permission_codes = [code.strip().upper() for code in raw_permissions.split(",") if code.strip()]
        elif isinstance(raw_permissions, list):
            permission_codes = [str(code).strip().upper() for code in raw_permissions if str(code).strip()]
        else:
            abort(400, description="permissions must be a comma-separated string or a list of codes.")
        permission_codes = list(dict.fromkeys(permission_codes))

        permission_rows = services.database_manager.execute_query(
            "SELECT permission_id, code, description FROM permissions"
        )
        permission_lookup = {
            str(row["code"]).upper(): row
            for row in permission_rows
        }
        unknown_codes = [code for code in permission_codes if code not in permission_lookup]
        if unknown_codes:
            valid_codes = ", ".join(sorted(permission_lookup))
            abort(400, description=f"Unknown permission code(s): {', '.join(unknown_codes)}. Valid codes: {valid_codes}")

        existing_roles = services.role_repository.find_all()
        if any(r.role_name.lower() == role_name.strip().lower() for r in existing_roles):
            abort(409, description=f"Role '{role_name.strip()}' already exists.")

        grants = [
            TableAccessGrant(
                grant_id=0,
                role_id=0,
                permission_id=int(permission_lookup[code]["permission_id"]),
                table_name=None,
                granted_by=user.user_id,
                granted_at=datetime.now(),
                permission=Permission(
                    permission_id=int(permission_lookup[code]["permission_id"]),
                    code=permission_lookup[code]["code"],
                    description=permission_lookup[code]["description"] or "",
                ),
                permission_code=permission_lookup[code]["code"],
            )
            for code in permission_codes
        ]

        new_role = Role(
            role_id=0,
            role_name=role_name.strip(),
            created_by=created_by,
            is_system_default=is_system_default,
            default_job_priority=priority,
            grants=grants,
        )
        saved = services.role_repository.save(new_role)
        return jsonify({
            "role_id": saved.role_id,
            "role_name": saved.role_name,
            "created_by": saved.created_by,
            "is_system_default": saved.is_system_default,
            "default_job_priority": saved.default_job_priority,
            "permissions": permission_codes,
        }), 201

    @app.put("/api/admin/roles/<int:role_id>")
    def update_role(role_id: int) -> Any:
        """Update a role's details and permission grants (Administrator only)."""
        admin, _role = require_permission("ADMIN", "roles")
        existing = services.role_repository.find_by_id(role_id)
        if existing is None:
            abort(404, description="Role not found.")

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        role_name = payload.get("role_name", existing.role_name)
        if not isinstance(role_name, str) or not role_name.strip():
            abort(400, description="role_name must be a non-empty string.")
        role_name = role_name.strip()

        priority = payload.get("default_job_priority", existing.default_job_priority)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            abort(400, description="default_job_priority must be an integer.")

        created_by = payload.get("created_by", existing.created_by)
        try:
            created_by = int(created_by)
        except (TypeError, ValueError):
            abort(400, description="created_by must be an integer user id.")
        if created_by != 0 and services.user_repository.find_by_id(created_by) is None:
            abort(400, description=f"created_by {created_by} does not match an existing user.")

        is_system_default = payload.get("is_system_default", existing.is_system_default)
        if isinstance(is_system_default, str):
            is_system_default = is_system_default.strip().lower() in {"true", "1", "yes"}
        is_system_default = bool(is_system_default)

        # Seeded built-in roles must stay system-protected and attributed to System.
        if existing.is_system_default and existing.created_by == 0:
            created_by = 0
            is_system_default = True

        duplicate = any(
            r.role_id != role_id and r.role_name.lower() == role_name.lower()
            for r in services.role_repository.find_all()
        )
        if duplicate:
            abort(409, description=f"Role '{role_name}' already exists.")

        permission_codes: list[str] | None = None
        permission_lookup: dict[str, Any] = {}
        if "permissions" in payload:
            raw_permissions = payload.get("permissions") or []
            if isinstance(raw_permissions, str):
                permission_codes = [code.strip().upper() for code in raw_permissions.split(",") if code.strip()]
            elif isinstance(raw_permissions, list):
                permission_codes = [str(code).strip().upper() for code in raw_permissions if str(code).strip()]
            else:
                abort(400, description="permissions must be a comma-separated string or a list of codes.")
            permission_codes = list(dict.fromkeys(permission_codes))

            permission_rows = services.database_manager.execute_query(
                "SELECT permission_id, code FROM permissions"
            )
            permission_lookup = {str(row["code"]).upper(): row for row in permission_rows}
            unknown_codes = [code for code in permission_codes if code not in permission_lookup]
            if unknown_codes:
                valid_codes = ", ".join(sorted(permission_lookup))
                abort(400, description=f"Unknown permission code(s): {', '.join(unknown_codes)}. Valid codes: {valid_codes}")

        services.database_manager.execute_query(
            """UPDATE roles
               SET role_name = ?, created_by = ?, is_system_default = ?, default_job_priority = ?
               WHERE role_id = ?""",
            (role_name, created_by, 1 if is_system_default else 0, priority, role_id),
        )

        if permission_codes is not None:
            services.database_manager.execute_query(
                "DELETE FROM table_access_grants WHERE role_id = ?", (role_id,)
            )
            granted_at = datetime.now().isoformat()
            for code in permission_codes:
                services.database_manager.execute_query(
                    """INSERT INTO table_access_grants
                       (role_id, permission_id, table_name, granted_by, granted_at)
                       VALUES (?, ?, NULL, ?, ?)""",
                    (role_id, int(permission_lookup[code]["permission_id"]), admin.user_id, granted_at),
                )

        updated = services.role_repository.find_by_id(role_id)
        granted_codes = sorted({
            grant.permission.code if grant.permission is not None else (grant.permission_code or "")
            for grant in (updated.grants if updated else [])
        } - {""})
        return jsonify({
            "role_id": role_id,
            "role_name": role_name,
            "created_by": created_by,
            "is_system_default": is_system_default,
            "default_job_priority": priority,
            "permissions": granted_codes,
        })

    @app.delete("/api/admin/roles/<int:role_id>")
    def delete_role(role_id: int) -> Any:
        """Delete an eligible custom role (Administrator only)."""
        require_permission("ADMIN", "roles")
        try:
            services.role_repository.delete(role_id)
        except KeyError:
            abort(404, description="Role not found.")
        except ValueError as exc:
            abort(400, description=str(exc))
        return "", 204

    @app.get("/health")
    def health() -> Any:
        """Report that the HTTP process is serving requests."""
        return jsonify(
            {
                "status": "ok",
                "cache": services.inventory_cache.stats(),
            }
        )

    @app.get("/api/cache/traffic")
    def cache_traffic() -> Any:
        return jsonify(request_metrics.snapshot())

    @app.post("/api/auth/register")
    def register() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        username = payload.get("username") or payload.get("name")
        email = payload.get("email", "")
        password = payload.get("password")

        if not isinstance(username, str) or not username.strip():
            abort(400, description="username must be a non-empty string.")
        if not isinstance(password, str) or len(password) < 8:
            abort(400, description="password must be at least 8 characters.")
        if not isinstance(email, str):
            email = ""

        try:
            user = services.authentication.register(
                username=username.strip(),
                email=email.strip(),
                password=password,
                role_id=3,  # Supervisor (read-only) — an admin promotes from the panel
            )
        except ValueError as exc:
            abort(409, description=str(exc))

        return jsonify({"user_id": user.user_id, "username": user.username, "role_id": user.role_id}), 201

    @app.post("/api/auth/login")
    def api_login() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        identifier = payload.get("username") or payload.get("email")
        password = payload.get("password")
        if not isinstance(identifier, str) or not isinstance(password, str):
            abort(400, description="username and password must be strings.")

        username = identifier.strip()
        if "@" in username:
            try:
                rows = services.database_manager.execute_query(
                    "SELECT username FROM users WHERE LOWER(email) = LOWER(?)",
                    (username,),
                )
                if rows:
                    username = str(rows[0]["username"])
            except Exception:
                pass

        try:
            session = services.authentication.login(username, password)
        except PermissionError:
            abort(401, description="Invalid username or password.")

        response = jsonify(
            {
                "session_token": session.session_token,
                "user_id": session.user_id,
                "roles": session.roles,
                "expires_at": session.expires_at.isoformat(),
            }
        )
        return set_session_cookie(response, session.session_token)

    # ------------------------------------------------------------------
    # Google / OpenID Connect sign-in
    # ------------------------------------------------------------------

    def google_login_service() -> Any:
        """Return the configured Google client, or fail with 503."""
        service = getattr(services, "google_login", None)
        if service is None:
            abort(503, description="Google sign-in is not configured on this server.")
        return service

    def google_login_redirect(*, session: Session | None = None, error: str = "") -> Any:
        """Send the browser back to the login page with a fragment payload.

        A fragment is used deliberately: it never reaches the server, never
        appears in a Referer header, and the login page clears it immediately.
        """
        params: dict[str, str] = {}
        if session is not None:
            params["session_token"] = session.session_token
        if error:
            params["error"] = error
        target = "/login"
        if params:
            target = "/login#" + urllib.parse.urlencode(params)
        response = redirect(target, code=302)
        response.headers["Cache-Control"] = "no-store"
        response.delete_cookie("google_oauth_state", path="/api/auth/google")
        if session is not None:
            set_session_cookie(response, session.session_token)
        return response

    def unique_username(email: str) -> str:
        """Derive a free local username from an email address."""
        base = re.sub(r"[^a-z0-9._-]+", "", (email.split("@")[0] or "").lower())
        base = base or "google-user"
        candidate = base
        for suffix in range(2, 100):
            if services.user_repository.find_by_username(candidate) is None:
                return candidate
            candidate = f"{base}{suffix}"
        return f"{base}-{int(time.time())}"

    def resolve_google_user(identity: Any, login_service: Any) -> User:
        """Link a verified Google identity to a local account.

        Existing accounts are matched by email. Unknown identities are
        provisioned with the least-privileged role unless auto-provisioning is
        disabled; a token never grants a role that the local database does not
        already assign.
        """
        if not identity.email:
            raise LookupError("The Google identity has no email address.")
        user = services.user_repository.find_by_email(identity.email)
        if user is not None:
            return user
        if not login_service.config.auto_provision:
            raise LookupError(f"No local account is linked to {identity.email}.")

        role_id = login_service.config.default_role_id
        if services.role_repository.find_by_id(role_id) is None:
            role_id = 3
        created = User(
            user_id=0,
            username=unique_username(identity.email),
            email=identity.email,
            role_id=role_id,
            created_at=datetime.now(),
        )
        return services.user_repository.save(created)

    def google_redirect_uri(service: Any) -> str:
        """Keep the callback on the host the browser is actually using.

        The registered redirect URI pins one host. When the app is opened via a
        different spelling of the same machine (``localhost`` instead of
        ``127.0.0.1``) the state cookie would be dropped at the callback, so the
        URI is rebuilt from the request host (same path, same scheme).
        """
        configured = service.config.redirect_uri
        parts = urllib.parse.urlsplit(configured)
        if request.host == parts.netloc:
            return configured
        return urllib.parse.urlunsplit(
            (request.scheme, request.host, parts.path, "", "")
        )

    @app.get("/api/auth/google/start")
    def google_login_start() -> Any:
        """Begin the authorization-code flow at the identity provider."""
        service = google_login_service()
        try:
            authorize_url, state = service.start(
                return_to=request.args.get("return_to", ""),
                redirect_uri=google_redirect_uri(service),
            )
        except Exception:
            app.logger.exception("Could not start the Google authorization request")
            abort(502, description="Google sign-in is temporarily unavailable.")
        response = redirect(authorize_url, code=302)
        response.headers["Cache-Control"] = "no-store"
        # Bind the state to this browser so a third party cannot replay a
        # callback URL into someone else's session (login CSRF).
        response.set_cookie(
            "google_oauth_state",
            state,
            max_age=300,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
            path="/api/auth/google",
        )
        return response

    @app.get("/api/auth/google/callback")
    def google_login_callback() -> Any:
        """Exchange the code, verify the ID token, and adopt a session."""
        service = google_login_service()
        from inventory.auth.google import GoogleTokenError
        from inventory.auth.google_auth import GoogleLoginError

        if request.args.get("error"):
            return google_login_redirect(error="Google sign-in was cancelled.")

        state = request.args.get("state", "")
        code = request.args.get("code", "")
        if not state or not code:
            return google_login_redirect(error="The Google response was incomplete.")

        expected_state = request.cookies.get("google_oauth_state", "")
        if not expected_state or not secrets.compare_digest(expected_state, state):
            return google_login_redirect(
                error="The sign-in request expired or did not start in this browser."
            )

        try:
            result = service.complete(state, code)
        except GoogleLoginError as error:
            app.logger.warning("Google sign-in failed: %s", error)
            return google_login_redirect(error=str(error))

        try:
            user = resolve_google_user(result.identity, service)
            session = Session.google_token(
                result.id_token,
                verifier=service.verifier,
                user_resolver=lambda _identity: user,
            )
        except LookupError as error:
            app.logger.info("Google sign-in rejected: %s", error)
            return google_login_redirect(error=str(error))
        except GoogleTokenError:
            app.logger.exception("Google ID token rejected after the code exchange")
            return google_login_redirect(error="The Google token could not be verified.")

        services.authentication.adopt_session(session)
        return google_login_redirect(session=session)

    @app.post("/api/auth/logout")
    def logout() -> Any:
        """Invalidate the presented session and clear the page cookie."""
        header = request.headers.get("Authorization", "")
        scheme, _, header_token = header.partition(" ")
        token = header_token if scheme.lower() == "bearer" and header_token else ""
        token = token or request.cookies.get(SESSION_COOKIE, "")

        if token:
            session = services.authentication.get_session(token)
            if session is not None:
                services.authentication.logout(session)

        response = make_response("", 204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/auth/me")
    def current_user() -> Any:
        """Report the session user's role and permission codes for UI gating."""
        session = require_session()
        user = services.user_repository.find_by_id(session.user_id)
        if user is None:
            abort(401, description="The session user no longer exists.")
        role = services.role_repository.find_by_id(user.role_id)
        if role is None:
            abort(403, description="The user's role no longer exists.")

        codes = sorted(
            {
                grant.permission.code
                for grant in role.grants
                if grant.permission is not None and grant.applies_to(None)
            }
        )
        return jsonify(
            {
                "user_id": user.user_id,
                "username": user.username,
                "email": user.email,
                "role_id": user.role_id,
                "role_name": role.role_name,
                "permissions": codes,
            }
        )

    @app.get("/api/permissions")
    def list_permissions() -> Any:
        require_session()
        rows = services.database_manager.execute_query(
            "SELECT permission_id, code, description FROM permissions ORDER BY permission_id ASC"
        )
        return jsonify([
            {
                "permission_id": r["permission_id"],
                "code": r["code"],
                "description": r["description"] or "",
            }
            for r in rows
        ])

    @app.post("/api/admin/permissions")
    def create_permission() -> Any:
        require_permission("ADMIN", "roles")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")
        code = payload.get("code")
        description = payload.get("description", "")
        if not isinstance(code, str) or not code.strip():
            abort(400, description="code must be a non-empty string.")
        code = code.strip().upper()
        existing = services.database_manager.execute_query(
            "SELECT permission_id FROM permissions WHERE UPPER(code) = UPPER(?)", (code,)
        )
        if existing:
            abort(409, description=f"Permission code '{code}' already exists.")
        services.database_manager.execute_query(
            "INSERT INTO permissions (code, description) VALUES (?, ?)",
            (code, str(description).strip()),
        )
        rows = services.database_manager.execute_query(
            "SELECT permission_id, code, description FROM permissions WHERE UPPER(code) = UPPER(?)", (code,)
        )
        row = rows[0]
        return jsonify({"permission_id": row["permission_id"], "code": row["code"], "description": row["description"]}), 201

    @app.put("/api/admin/permissions/<int:permission_id>")
    def update_permission(permission_id: int) -> Any:
        """Update a permission's description. Code is immutable after creation."""
        require_permission("ADMIN", "roles")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")
        description = payload.get("description")
        if not isinstance(description, str):
            abort(400, description="description must be a string.")
        description = description.strip()
        
        # Verify permission exists
        existing = services.database_manager.execute_query(
            "SELECT permission_id, code FROM permissions WHERE permission_id = ?", (permission_id,)
        )
        if not existing:
            abort(404, description=f"Permission ID {permission_id} not found.")
        
        # Update only the description
        services.database_manager.execute_query(
            "UPDATE permissions SET description = ? WHERE permission_id = ?",
            (description, permission_id),
        )
        
        # Fetch and return updated permission
        rows = services.database_manager.execute_query(
            "SELECT permission_id, code, description FROM permissions WHERE permission_id = ?", (permission_id,)
        )
        row = rows[0]
        return jsonify({"permission_id": row["permission_id"], "code": row["code"], "description": row["description"]})

    @app.delete("/api/admin/permissions/<int:permission_id>")
    def delete_permission(permission_id: int) -> Any:
        """Delete a permission if not in use by any role."""
        require_permission("ADMIN", "roles")
        
        # Verify permission exists
        existing = services.database_manager.execute_query(
            "SELECT permission_id, code FROM permissions WHERE permission_id = ?", (permission_id,)
        )
        if not existing:
            abort(404, description=f"Permission ID {permission_id} not found.")
        
        # Check if permission is referenced in table_access_grants
        usage_check = services.database_manager.execute_query(
            "SELECT COUNT(*) as usage_count FROM table_access_grants WHERE permission_id = ?",
            (permission_id,)
        )
        usage_count = usage_check[0]["usage_count"] if usage_check else 0
        
        if usage_count > 0:
            abort(409, description="Cannot delete permission: it is currently assigned to one or more roles")
        
        # Delete the permission
        services.database_manager.execute_query(
            "DELETE FROM permissions WHERE permission_id = ?", (permission_id,)
        )
        
        return "", 204

    @app.get("/api/products")
    def list_products() -> Any:
        require_permission("VIEW", "products")
        try:
            supplier_value = request.args.get("supplier_id")
            min_price_value = request.args.get("min_price")
            max_price_value = request.args.get("max_price")
            search_query = request.args.get("search_term") or request.args.get("q")
            is_unpaginated = request.args.get("all", "").lower() in {"1", "true", "yes"}

            page_raw = request.args.get("page")
            page_size_raw = request.args.get("page_size") or request.args.get("limit")

            page = int(page_raw) if page_raw is not None else 1
            if page < 1:
                page = 1
            page_size = int(page_size_raw) if page_size_raw is not None else 10
            if page_size < 1:
                page_size = 10
            elif page_size > 100:
                page_size = 100

            limit = None if is_unpaginated else page_size
            offset = None if is_unpaginated else (page - 1) * page_size

            filters = ProductFilter(
                category=request.args.get("category"),
                supplier_id=int(supplier_value) if supplier_value else None,
                low_stock_only=request.args.get("low_stock_only", "false").lower()
                in {"1", "true", "yes"},
                min_price=Decimal(min_price_value) if min_price_value else None,
                max_price=Decimal(max_price_value) if max_price_value else None,
                search_term=search_query,
                limit=limit,
                offset=offset,
            )
        except (InvalidOperation, ValueError):
            abort(400, description="Invalid product filter or pagination value.")

        if is_unpaginated:
            products = services.product_repository.find_all(filters)
            response = jsonify([product_payload(product) for product in products])
        else:
            import math

            total_items = services.product_repository.count(filters)
            total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1
            products = services.product_repository.find_all(filters)
            response = jsonify(
                {
                    "items": [product_payload(product) for product in products],
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total_items": total_items,
                        "total_pages": total_pages,
                        "has_next": page < total_pages,
                        "has_prev": page > 1,
                    },
                }
            )

        response.headers["X-Cache"] = getattr(
            services.product_repository, "last_cache_status", "MISS"
        )
        return response

    @app.get("/api/suppliers")
    def list_suppliers() -> Any:
        require_permission("VIEW", "suppliers")
        return jsonify([
            {
                "supplier_id": s.supplier_id,
                "name": s.name,
                "contact_email": s.contact_email,
                "phone": s.phone,
                "address": s.address
            }
            for s in services.supplier_repository.find_all()
        ])

    @app.post("/api/admin/suppliers")
    def create_supplier() -> Any:
        """Create a new supplier."""
        require_permission("APPEND", "suppliers")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")
        
        name = payload.get("name", "").strip()
        contact_email = payload.get("contact_email", "").strip()
        phone = payload.get("phone", "").strip()
        address = payload.get("address", "").strip()
        
        if not name:
            abort(400, description="name is required.")
        if not contact_email:
            abort(400, description="contact_email is required.")
        if not phone:
            abort(400, description="phone is required.")
        if not address:
            abort(400, description="address is required.")
        
        from inventory.catalog.models import Supplier
        supplier = Supplier(
            supplier_id=0,
            name=name,
            contact_email=contact_email,
            phone=phone,
            address=address
        )
        
        created = services.supplier_repository.save(supplier)
        return jsonify({
            "supplier_id": created.supplier_id,
            "name": created.name,
            "contact_email": created.contact_email,
            "phone": created.phone,
            "address": created.address
        }), 201

    @app.put("/api/admin/suppliers/<int:supplier_id>")
    def update_supplier(supplier_id: int) -> Any:
        """Update an existing supplier."""
        require_permission("EDIT", "suppliers")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")
        
        # Verify supplier exists
        existing = services.supplier_repository.find_by_id(supplier_id)
        if existing is None:
            abort(404, description=f"Supplier ID {supplier_id} not found.")
        
        name = payload.get("name", "").strip()
        contact_email = payload.get("contact_email", "").strip()
        phone = payload.get("phone", "").strip()
        address = payload.get("address", "").strip()
        
        if not name:
            abort(400, description="name is required.")
        if not contact_email:
            abort(400, description="contact_email is required.")
        if not phone:
            abort(400, description="phone is required.")
        if not address:
            abort(400, description="address is required.")
        
        from inventory.catalog.models import Supplier
        supplier = Supplier(
            supplier_id=supplier_id,
            name=name,
            contact_email=contact_email,
            phone=phone,
            address=address
        )
        
        updated = services.supplier_repository.save(supplier)
        return jsonify({
            "supplier_id": updated.supplier_id,
            "name": updated.name,
            "contact_email": updated.contact_email,
            "phone": updated.phone,
            "address": updated.address
        })

    @app.delete("/api/admin/suppliers/<int:supplier_id>")
    def delete_supplier(supplier_id: int) -> Any:
        """Delete a supplier if no products reference it."""
        require_permission("DELETE", "suppliers")
        
        # Verify supplier exists
        existing = services.supplier_repository.find_by_id(supplier_id)
        if existing is None:
            abort(404, description=f"Supplier ID {supplier_id} not found.")
        
        # Repository.delete() already validates product references
        try:
            services.supplier_repository.delete(supplier_id)
            return "", 204
        except ValueError as e:
            # Repository raises ValueError if products reference this supplier
            abort(409, description=str(e))

    @app.get("/api/products/<int:product_id>")
    def get_product(product_id: int) -> Any:
        require_permission("VIEW", "products")
        product = services.product_repository.find_by_id(product_id)
        if product is None:
            abort(404, description="Product not found.")
        response = jsonify(product_payload(product))
        response.headers["X-Cache"] = getattr(services.product_repository, "last_cache_status", "MISS")
        return response

    @app.post("/api/cache/benchmark")
    def benchmark_cache() -> Any:
        require_permission("EDIT", "products")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        iterations = payload.get("iterations", 100)
        if type(iterations) is not int or not 10 <= iterations <= 150:
            abort(400, description="iterations must be an integer from 10 to 150.")

        product_rows = services.database_manager.execute_query(
            "SELECT product_id FROM products ORDER BY product_id ASC LIMIT 1"
        )
        if not product_rows:
            abort(404, description="Add a product before running the cache benchmark.")
        product_id = int(product_rows[0]["product_id"])

        lock_token = services.lock_manager.acquire(
            str(product_id),
            owner_id=f"cache-benchmark:{time.monotonic_ns()}",
            timeout_ms=1000,
        )
        if lock_token is None:
            abort(503, description="Product is busy; try the benchmark again shortly.")

        try:
            product = services.base_product_repository.find_by_id(product_id)
            if product is None:
                abort(404, description="No product is available for benchmarking.")

            services.inventory_cache.invalidate_product(product_id)
            warm_started = time.perf_counter_ns()
            cached_product = services.product_repository.find_by_id(product_id)
            warm_elapsed_ns = time.perf_counter_ns() - warm_started
            warm_read_ms = warm_elapsed_ns / 1_000_000
            first_read_status = services.product_repository.last_cache_status
            if cached_product is None:
                abort(404, description="No product is available for benchmarking.")

            stats_before = services.inventory_cache.stats()
            database_elapsed_ns = 0
            cache_elapsed_ns = 0
            sample_step = max(1, iterations // 40)
            series = [
                {
                    "requests": 0,
                    "database_total_ms": 0.0,
                    "cache_total_ms": warm_read_ms,
                    "efficiency_percent": 0.0,
                }
            ]
            for index in range(iterations):
                if index % 2 == 0:
                    started = time.perf_counter_ns()
                    services.base_product_repository.find_by_id(product_id)
                    database_elapsed_ns += time.perf_counter_ns() - started

                    started = time.perf_counter_ns()
                    services.product_repository.find_by_id(product_id)
                    cache_elapsed_ns += time.perf_counter_ns() - started
                else:
                    started = time.perf_counter_ns()
                    services.product_repository.find_by_id(product_id)
                    cache_elapsed_ns += time.perf_counter_ns() - started

                    started = time.perf_counter_ns()
                    services.base_product_repository.find_by_id(product_id)
                    database_elapsed_ns += time.perf_counter_ns() - started

                completed = index + 1
                if completed % sample_step == 0 or completed == iterations:
                    database_total_ms = database_elapsed_ns / 1_000_000
                    cache_total_ms = (
                        warm_elapsed_ns + cache_elapsed_ns
                    ) / 1_000_000
                    series.append(
                        {
                            "requests": completed,
                            "database_total_ms": database_total_ms,
                            "cache_total_ms": cache_total_ms,
                            "efficiency_percent": (
                                (database_total_ms - cache_total_ms)
                                / database_total_ms
                                * 100
                                if database_total_ms
                                else 0.0
                            ),
                        }
                    )

            stats_after = services.inventory_cache.stats()
            database_reads = iterations
            cache_reads = iterations
            database_avg_ms = database_elapsed_ns / database_reads / 1_000_000
            cache_avg_ms = cache_elapsed_ns / cache_reads / 1_000_000
            database_total_ms = database_elapsed_ns / 1_000_000
            cache_total_ms = (
                warm_elapsed_ns + cache_elapsed_ns
            ) / 1_000_000
            improvement_percent = (
                (database_total_ms - cache_total_ms) / database_total_ms * 100
                if database_total_ms > 0
                else 0.0
            )
            hit_delta = int(stats_after["hits"]) - int(stats_before["hits"])
            miss_delta = int(stats_after["misses"]) - int(stats_before["misses"])
            cache_hit_rate = (
                hit_delta / (hit_delta + miss_delta)
                if hit_delta + miss_delta
                else 0.0
            )
            break_even_reads = None
            per_read_savings_ms = database_avg_ms - cache_avg_ms
            if per_read_savings_ms > 0:
                break_even_reads = max(
                    0,
                    int(warm_read_ms / per_read_savings_ms) + 1,
                )

            return jsonify(
                {
                    "iterations": iterations,
                    "database": {
                        "label": "Database only",
                        "reads": database_reads,
                        "total_ms": database_total_ms,
                        "average_ms": database_avg_ms,
                    },
                    "cache": {
                        "label": "Cache-aside (fill + reads)",
                        "reads": cache_reads,
                        "total_ms": cache_total_ms,
                        "average_ms": cache_avg_ms,
                        "policy": stats_after["policy"],
                        "hit_rate": cache_hit_rate,
                        "local_hits": (
                            int(stats_after["local_hits"])
                            - int(stats_before["local_hits"])
                        ),
                        "redis_hits": (
                            int(stats_after["redis_hits"])
                            - int(stats_before["redis_hits"])
                        ),
                        "first_read_ms": warm_read_ms,
                        "first_read_status": first_read_status,
                    },
                    "improvement_percent": improvement_percent,
                    "break_even_reads": break_even_reads,
                    "series": series,
                    "cache_stats": stats_after,
                }
            )
        finally:
            services.lock_manager.release(lock_token)

    @app.post("/api/products")
    def create_product() -> Any:
        require_permission("APPEND", "products")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        product = product_from_payload(payload, product_id=0)
        if sku_taken(product.sku):
            abort(409, description="A product with this SKU already exists.")

        created = services.product_repository.save(product)
        return jsonify(product_payload(created)), 201

    @app.put("/api/products/<int:product_id>")
    def update_product(product_id: int) -> Any:
        require_permission("EDIT", "products")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        existing = services.product_repository.find_by_id(product_id)
        if existing is None:
            abort(404, description="Product not found.")

        product = product_from_payload(payload, product_id=product_id)
        if sku_taken(product.sku, exclude_product_id=product_id):
            abort(409, description="A product with this SKU already exists.")

        updated = services.product_repository.save(product)
        return jsonify(product_payload(updated))

    @app.delete("/api/products/<int:product_id>")
    def delete_product(product_id: int) -> Any:
        require_permission("DELETE", "products")
        try:
            services.product_repository.delete(product_id)
        except KeyError:
            abort(404, description="Product not found.")
        except ValueError as error:
            abort(409, description=str(error))
        return "", 204

    @app.post("/api/sales")
    def submit_sale() -> Any:
        user, role = require_permission("APPEND", "sales")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        try:
            product_id = int(payload["product_id"])
            quantity = int(payload["quantity"])
        except (KeyError, TypeError, ValueError):
            abort(400, description="product_id and quantity must be integers.")

        customer_ref = payload.get("customer_ref", "")
        if not isinstance(customer_ref, str) or quantity < 1:
            abort(400, description="quantity must be positive and customer_ref a string.")

        try:
            job_id = submit_sale_job(
                services,
                user,
                role,
                product_id,
                quantity,
                customer_ref,
            )
        except LookupError:
            abort(404, description="Product not found.")
        except ValueError as error:
            abort(400, description=str(error))

        return jsonify({"job_id": job_id, "status": "pending"}), 202

    # ==================================================================
    # Analytics & Statistical Intelligence Endpoints
    # ==================================================================

    def get_analytics() -> Any:
        if services.analytics_service is not None:
            return services.analytics_service
        from inventory.analytics.service import AnalyticsService
        return AnalyticsService(
            database_manager=services.database_manager,
            product_repository=services.base_product_repository,
            transaction_repository=services.transaction_repository,
            supplier_repository=services.supplier_repository,
        )

    @app.get("/api/analytics/overview")
    def analytics_inventory_overview() -> Any:
        require_permission("VIEW", "products")
        analytics = get_analytics()
        return jsonify(analytics.get_inventory_overview())

    @app.get("/api/analytics/sales")
    def analytics_sales() -> Any:
        require_permission("VIEW", "products")
        days_str = request.args.get("days", "30")
        try:
            days = int(days_str)
        except ValueError:
            days = 30
        analytics = get_analytics()
        return jsonify(analytics.get_sales_analytics(days=days))

    @app.get("/api/analytics/purchases")
    def analytics_purchases() -> Any:
        require_permission("VIEW", "products")
        days_str = request.args.get("days", "30")
        try:
            days = int(days_str)
        except ValueError:
            days = 30
        analytics = get_analytics()
        return jsonify(analytics.get_purchase_analytics(days=days))

    @app.get("/api/analytics/suppliers")
    def analytics_suppliers() -> Any:
        require_permission("VIEW", "products")
        analytics = get_analytics()
        return jsonify(analytics.get_supplier_analytics())

    @app.get("/api/analytics/stock-movement")
    def analytics_stock_movement() -> Any:
        require_permission("VIEW", "products")
        days_str = request.args.get("days", "30")
        try:
            days = int(days_str)
        except ValueError:
            days = 30
        analytics = get_analytics()
        return jsonify(analytics.get_stock_movement_analytics(days=days))

    @app.get("/api/analytics/financials")
    def analytics_financials() -> Any:
        require_permission("VIEW", "products")
        analytics = get_analytics()
        return jsonify(analytics.get_financial_analytics())

    @app.get("/api/analytics/alerts")
    def analytics_alerts() -> Any:
        require_permission("VIEW", "products")
        analytics = get_analytics()
        return jsonify(analytics.get_inventory_alerts())

    @app.get("/api/analytics/abc")
    def analytics_abc_classification() -> Any:
        require_permission("VIEW", "products")
        analytics = get_analytics()
        return jsonify(analytics.get_abc_analysis())

    @app.get("/api/analytics/forecast")
    def analytics_demand_forecast() -> Any:
        require_permission("VIEW", "products")
        pid_raw = request.args.get("product_id")
        horizon_raw = request.args.get("horizon", "30")
        product_id = int(pid_raw) if pid_raw and pid_raw.isdigit() else None
        try:
            horizon = int(horizon_raw)
        except ValueError:
            horizon = 30
        analytics = get_analytics()
        return jsonify(analytics.get_demand_forecast(product_id=product_id, horizon_days=horizon))

    @app.get("/api/analytics/reorder-recommendations")
    def analytics_reorder_recommendations() -> Any:
        require_permission("VIEW", "products")
        sl_raw = request.args.get("service_level", "0.95")
        try:
            service_level = float(sl_raw)
        except ValueError:
            service_level = 0.95
        analytics = get_analytics()
        return jsonify(analytics.get_reorder_recommendations(service_level=service_level))

    return app


def main(argv: Sequence[str] | None = None) -> None:
    """Start the Flask development server using the shared application builder."""
    env_port = os.environ.get("PORT")
    env_host = os.environ.get("HOST")
    default_port = int(env_port) if env_port else 5000
    default_host = env_host if env_host else ("0.0.0.0" if env_port else "127.0.0.1")

    parser = argparse.ArgumentParser(description="Smart Inventory Management API")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args(argv)

    try:
        from inventory.app.main import build_application

        services = build_application()
    except NotImplementedError:
        print(
            "Server startup is wired, but database foundation methods are still TODO. "
            "Complete them before serving requests."
        )
        return

    create_app(services).run(
        host=args.host,
        port=args.port,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()