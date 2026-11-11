"""Optional Flask backend API for the inventory application."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any

from inventory.access_control.models import Role
from inventory.app.services import ApplicationServices
from inventory.app.workflows import submit_sale_job
from inventory.auth.models import Session, User
from inventory.catalog.filters import ProductFilter


EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def create_app(services: ApplicationServices | None = None) -> Any:
    """Create an HTTP app around an already-constructed service graph."""
    if services is None:
        from inventory.app.main import build_application
        services = build_application()

    import os
    from flask import Flask, abort, jsonify, request

    # Resolve templates/ and static/ relative to this file (src/inventory/app/)
    _here = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(_here, "templates"),
        static_folder=os.path.join(_here, "static"),
        static_url_path="/static",
    )

    # Register frontend page routes
    from inventory.app.web import register_page_routes
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

    @app.get("/health")
    def health() -> Any:
        """Report that the HTTP process is serving requests."""
        return jsonify(
            {
                "status": "ok",
                "cache": services.inventory_cache.stats(),
            }
        )

    @app.post("/api/auth/register")
    def register() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description="Expected a JSON object.")

        username = payload.get("username")
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

        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            abort(400, description="username and password must be strings.")

        try:
            session = services.authentication.login(username, password)
        except PermissionError:
            abort(401, description="Invalid username or password.")

        return jsonify(
            {
                "session_token": session.session_token,
                "user_id": session.user_id,
                "roles": session.roles,
                "expires_at": session.expires_at.isoformat(),
            }
        )

    @app.post("/api/auth/logout")
    def logout() -> Any:
        session = require_session()
        services.authentication.logout(session)
        return "", 204

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
                "role_id": user.role_id,
                "role_name": role.role_name,
                "permissions": codes,
            }
        )

    @app.get("/api/products")
    def list_products() -> Any:
        require_permission("VIEW", "products")
        try:
            supplier_value = request.args.get("supplier_id")
            min_price_value = request.args.get("min_price")
            max_price_value = request.args.get("max_price")
            filters = ProductFilter(
                category=request.args.get("category"),
                supplier_id=int(supplier_value) if supplier_value else None,
                low_stock_only=request.args.get("low_stock_only", "false").lower()
                in {"1", "true", "yes"},
                min_price=Decimal(min_price_value) if min_price_value else None,
                max_price=Decimal(max_price_value) if max_price_value else None,
                search_term=request.args.get("search_term"),
            )
        except (InvalidOperation, ValueError):
            abort(400, description="Invalid product filter value.")

        products = services.product_repository.find_all(filters)
        response = jsonify([product_payload(product) for product in products])
        response.headers["X-Cache"] = services.product_repository.last_cache_status
        return response

    @app.get("/api/suppliers")
    def list_suppliers() -> Any:
        require_permission("VIEW", "suppliers")
        return jsonify([
            {"supplier_id": s.supplier_id, "name": s.name}
            for s in services.supplier_repository.find_all()
        ])

    @app.get("/api/products/<int:product_id>")
    def get_product(product_id: int) -> Any:
        require_permission("VIEW", "products")
        product = services.product_repository.find_by_id(product_id)
        if product is None:
            abort(404, description="Product not found.")
        response = jsonify(product_payload(product))
        response.headers["X-Cache"] = services.product_repository.last_cache_status
        return response

    @app.post("/api/cache/benchmark")
    def benchmark_cache() -> Any:
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