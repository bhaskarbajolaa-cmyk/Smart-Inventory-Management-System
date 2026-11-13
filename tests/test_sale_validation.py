"""Unit tests verifying rejection of sales when requested quantity exceeds available stock."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from inventory.app.main import build_application
from inventory.app.server import create_app
from inventory.app.workflows import submit_sale_job
from inventory.catalog.models import Product


class TestSaleOversellingPrevention(unittest.TestCase):
    """Test that selling more units than available is rejected synchronously."""

    def setUp(self) -> None:
        self.services = build_application()
        self.app = create_app(self.services)
        self.client = self.app.test_client()

        # Login as admin to get session token
        resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.token = resp.json["session_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

        # Create a test product with unique SKU and exactly 30 units in stock
        supplier = self.services.supplier_repository.find_all()[0]
        self.sku = f"WIDGET-{uuid4().hex[:8]}"
        self.product = self.services.product_repository.save(
            Product(
                product_id=0,
                name="Limited Widget",
                sku=self.sku,
                category="Widgets",
                quantity=30,
                reorder_level=5,
                unit_price=Decimal("15.00"),
                supplier_id=supplier.supplier_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        self.user = self.services.user_repository.find_by_username("admin")
        self.role = self.services.role_repository.find_by_id(self.user.role_id)

    def tearDown(self) -> None:
        try:
            self.services.product_repository.delete(self.product.product_id)
        except Exception:
            pass

    def test_submit_sale_job_rejects_overselling(self) -> None:
        """submit_sale_job must raise ValueError when quantity > product.quantity."""
        with self.assertRaises(ValueError) as cm:
            submit_sale_job(
                services=self.services,
                user=self.user,
                role=self.role,
                product_id=self.product.product_id,
                quantity=40,  # 40 requested, only 30 available
                customer_ref="TEST-REF",
            )
        self.assertIn("Insufficient stock", str(cm.exception))
        self.assertIn("Available: 30", str(cm.exception))

        # Ensure stock remained 30
        refreshed = self.services.product_repository.find_by_id(self.product.product_id)
        self.assertEqual(refreshed.quantity, 30)

    def test_api_sales_returns_400_on_overselling(self) -> None:
        """POST /api/sales must return HTTP 400 with descriptive error message."""
        resp = self.client.post(
            "/api/sales",
            headers=self.headers,
            json={
                "product_id": self.product.product_id,
                "quantity": 40,
                "customer_ref": "CUST-OVERSELL",
            },
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json
        self.assertIn("Insufficient stock", data.get("description", ""))
        self.assertEqual(data.get("error"), "Bad Request")

        # Ensure stock in database remained untouched
        refreshed = self.services.product_repository.find_by_id(self.product.product_id)
        self.assertEqual(refreshed.quantity, 30)

    def test_api_sales_accepts_valid_quantity(self) -> None:
        """POST /api/sales returns HTTP 202 when quantity is within available stock."""
        resp = self.client.post(
            "/api/sales",
            headers=self.headers,
            json={
                "product_id": self.product.product_id,
                "quantity": 5,
                "customer_ref": "CUST-VALID",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.assertIn("job_id", resp.json)
        self.assertEqual(resp.json.get("status"), "pending")
