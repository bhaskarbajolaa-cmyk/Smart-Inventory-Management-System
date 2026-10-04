"""Optional Flask backend API for the inventory application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from inventory.access_control.models import Role
from inventory.app.services import ApplicationServices
from inventory.app.workflows import submit_sale_job
from inventory.auth.models import Session, User
from inventory.catalog.filters import ProductFilter


def create_app(services: ApplicationServices) -> Any:
    """Create an HTTP app around an already-constructed service graph."""
    from flask import Flask, abort, jsonify, request

    app = Flask(__name__)

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

    @app.get("/health")
    def health() -> Any:
        """Report that the HTTP process is serving requests."""
        return jsonify({"status": "ok"})

    @app.post("/api/auth/login")
    def login() -> Any:
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
        return jsonify([product_payload(product) for product in products])

    @app.get("/api/products/<int:product_id>")
    def get_product(product_id: int) -> Any:
        require_permission("VIEW", "products")
        product = services.product_repository.find_by_id(product_id)
        if product is None:
            abort(404, description="Product not found.")
        return jsonify(product_payload(product))

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
    parser = argparse.ArgumentParser(description="Smart Inventory Management API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
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