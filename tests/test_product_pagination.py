"""Integration and unit tests for server-side product pagination and search."""

from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from inventory.app.main import build_application
from inventory.app.server import create_app
from inventory.catalog.filters import ProductFilter
from inventory.catalog.models import Product
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository


class TestProductRepositoryPagination(unittest.TestCase):
    """Test limit, offset, and count in ProductRepository directly."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_name=":memory:")
        self.db.connect()
        self.repo = ProductRepository(self.db)

        # Seed supplier
        self.db.execute_query(
            "INSERT INTO suppliers (supplier_id, name, contact_email, phone, address) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, "Acme Corp", "acme@example.com", "555-1234", "123 Acme St"),
        )

        # Seed 15 products
        now = datetime.now()
        for i in range(1, 16):
            self.repo.save(
                Product(
                    product_id=i,
                    name=f"Widget {i:02d}",
                    sku=f"SKU-{i:03d}",
                    category="Gadgets" if i % 2 == 0 else "Tools",
                    quantity=10 * i,
                    reorder_level=15,
                    unit_price=Decimal(str(5.0 * i)),
                    supplier_id=1,
                    updated_at=now,
                )
            )

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_count_all_products(self) -> None:
        total = self.repo.count()
        self.assertEqual(total, 15)

    def test_count_with_category_filter(self) -> None:
        gadgets_count = self.repo.count(ProductFilter(category="Gadgets"))
        tools_count = self.repo.count(ProductFilter(category="Tools"))
        self.assertEqual(gadgets_count, 7)
        self.assertEqual(tools_count, 8)

    def test_count_with_search_term(self) -> None:
        count = self.repo.count(ProductFilter(search_term="Widget 0"))
        self.assertEqual(count, 9)

    def test_find_all_with_limit_and_offset(self) -> None:
        # Page 1: 5 items
        page1 = self.repo.find_all(ProductFilter(limit=5, offset=0))
        self.assertEqual(len(page1), 5)
        self.assertEqual(page1[0].product_id, 1)
        self.assertEqual(page1[-1].product_id, 5)

        # Page 2: 5 items
        page2 = self.repo.find_all(ProductFilter(limit=5, offset=5))
        self.assertEqual(len(page2), 5)
        self.assertEqual(page2[0].product_id, 6)
        self.assertEqual(page2[-1].product_id, 10)

        # Page 3: 5 items
        page3 = self.repo.find_all(ProductFilter(limit=5, offset=10))
        self.assertEqual(len(page3), 5)
        self.assertEqual(page3[0].product_id, 11)
        self.assertEqual(page3[-1].product_id, 15)

        # Page 4: empty
        page4 = self.repo.find_all(ProductFilter(limit=5, offset=15))
        self.assertEqual(len(page4), 0)


class TestProductApiPagination(unittest.TestCase):
    """Test GET /api/products server-side pagination and search."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.services = build_application()
        cls.app = create_app(cls.services)

    def setUp(self) -> None:
        self.client = self.app.test_client()
        # Log in as admin (role 1 has VIEW on products)
        login_res = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin1234"}
        )
        self.assertEqual(login_res.status_code, 200)
        self.token = login_res.get_json()["session_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_default_pagination_structure(self) -> None:
        res = self.client.get("/api/products", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("items", data)
        self.assertIn("pagination", data)
        pagination = data["pagination"]
        self.assertEqual(pagination["page"], 1)
        self.assertEqual(pagination["page_size"], 10)
        self.assertIsInstance(pagination["total_items"], int)
        self.assertIsInstance(pagination["total_pages"], int)
        self.assertIn("has_next", pagination)
        self.assertIn("has_prev", pagination)

    def test_pagination_parameters(self) -> None:
        res = self.client.get("/api/products?page=1&page_size=2", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["items"]), 2)
        pagination = data["pagination"]
        self.assertEqual(pagination["page"], 1)
        self.assertEqual(pagination["page_size"], 2)
        if pagination["total_items"] > 2:
            self.assertTrue(pagination["has_next"])
        self.assertFalse(pagination["has_prev"])

    def test_pagination_page_2(self) -> None:
        res = self.client.get("/api/products?page=2&page_size=2", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["pagination"]["page"], 2)
        self.assertTrue(data["pagination"]["has_prev"])

    def test_server_side_search_parameter(self) -> None:
        # Search via 'search_term' and alias 'q'
        res1 = self.client.get("/api/products?search_term=Laptop", headers=self.headers)
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()

        res2 = self.client.get("/api/products?q=Laptop", headers=self.headers)
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()

        self.assertEqual(len(data1["items"]), len(data2["items"]))

    def test_unpaginated_fallback_all_param(self) -> None:
        res = self.client.get("/api/products?all=true", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsInstance(data, list)

    def test_invalid_pagination_values_handled(self) -> None:
        # Negative page or page_size clamp cleanly or 400
        res = self.client.get("/api/products?page=invalid", headers=self.headers)
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
