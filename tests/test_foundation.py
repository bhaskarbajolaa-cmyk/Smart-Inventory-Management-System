"""Comprehensive tests for Foundation module: Catalog models, filters, DatabaseManager, and Repositories."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from inventory.catalog.filters import ProductFilter
from inventory.catalog.models import Product, Supplier
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.suppliers import SupplierRepository


class TestCatalogModels(unittest.TestCase):
    """Unit tests for Product and Supplier domain models and ProductFilter."""

    def test_product_creation_and_attributes(self) -> None:
        now = datetime.now()
        product = Product(
            product_id=1,
            name="Wireless Mouse",
            sku="WM-100",
            category="Electronics",
            quantity=50,
            reorder_level=10,
            unit_price=Decimal("29.99"),
            supplier_id=1,
            updated_at=now,
        )
        self.assertEqual(product.product_id, 1)
        self.assertEqual(product.name, "Wireless Mouse")
        self.assertEqual(product.sku, "WM-100")
        self.assertEqual(product.quantity, 50)
        self.assertEqual(product.reorder_level, 10)
        self.assertEqual(product.unit_price, Decimal("29.99"))

    def test_product_price_float_conversion(self) -> None:
        now = datetime.now()
        product = Product(
            product_id=1,
            name="Mouse Pad",
            sku="MP-101",
            category="Accessories",
            quantity=10,
            reorder_level=2,
            unit_price=9.99,  # type: ignore[arg-type]
            supplier_id=1,
            updated_at=now,
        )
        self.assertIsInstance(product.unit_price, Decimal)
        self.assertEqual(product.unit_price, Decimal("9.99"))

    def test_product_validation_negative_values(self) -> None:
        now = datetime.now()
        with self.assertRaises(ValueError):
            Product(1, "Bad", "B-1", "Cat", -1, 5, Decimal("10"), 1, now)

        with self.assertRaises(ValueError):
            Product(1, "Bad", "B-1", "Cat", 10, -5, Decimal("10"), 1, now)

        with self.assertRaises(ValueError):
            Product(1, "Bad", "B-1", "Cat", 10, 5, Decimal("-10"), 1, now)

    def test_update_stock_increase_and_decrease(self) -> None:
        now = datetime.now()
        product = Product(1, "Keyboard", "KB-1", "Electronics", 20, 5, Decimal("49.99"), 1, now)

        product.update_stock(10)
        self.assertEqual(product.quantity, 30)

        product.update_stock(-5)
        self.assertEqual(product.quantity, 25)

    def test_update_stock_rejects_negative_total(self) -> None:
        now = datetime.now()
        product = Product(1, "Keyboard", "KB-1", "Electronics", 10, 5, Decimal("49.99"), 1, now)
        with self.assertRaises(ValueError):
            product.update_stock(-15)
        self.assertEqual(product.quantity, 10)

    def test_is_low_stock(self) -> None:
        now = datetime.now()
        product = Product(1, "Monitor", "MN-1", "Electronics", 15, 10, Decimal("199.99"), 1, now)
        self.assertFalse(product.is_low_stock())

        product.update_stock(-5)  # quantity = 10, reorder_level = 10
        self.assertTrue(product.is_low_stock())

        product.update_stock(-5)  # quantity = 5, reorder_level = 10
        self.assertTrue(product.is_low_stock())

    def test_supplier_creation_and_validation(self) -> None:
        supplier = Supplier(
            supplier_id=1,
            name="Tech Supplies Inc",
            contact_email="contact@techsupplies.com",
            phone="123-456-7890",
            address="123 Tech Lane",
        )
        self.assertEqual(supplier.supplier_id, 1)
        self.assertEqual(supplier.name, "Tech Supplies Inc")

        with self.assertRaises(ValueError):
            Supplier(2, "", "test@example.com", "123", "Address")

    def test_product_filter_matches_helper(self) -> None:
        now = datetime.now()
        product = Product(
            product_id=1,
            name="Ergonomic Chair",
            sku="CHAIR-01",
            category="Furniture",
            quantity=4,
            reorder_level=5,
            unit_price=Decimal("150.00"),
            supplier_id=2,
            updated_at=now,
        )

        # Matching filters
        self.assertTrue(ProductFilter().matches(product))
        self.assertTrue(ProductFilter(category="furniture").matches(product))
        self.assertTrue(ProductFilter(supplier_id=2).matches(product))
        self.assertTrue(ProductFilter(low_stock_only=True).matches(product))
        self.assertTrue(ProductFilter(min_price=Decimal("100"), max_price=Decimal("200")).matches(product))
        self.assertTrue(ProductFilter(search_term="ergonomic").matches(product))
        self.assertTrue(ProductFilter(search_term="chair-01").matches(product))

        # Non-matching filters
        self.assertFalse(ProductFilter(category="Electronics").matches(product))
        self.assertFalse(ProductFilter(supplier_id=1).matches(product))
        self.assertFalse(ProductFilter(min_price=Decimal("200")).matches(product))
        self.assertFalse(ProductFilter(max_price=Decimal("100")).matches(product))
        self.assertFalse(ProductFilter(search_term="desk").matches(product))


class TestDatabaseManager(unittest.TestCase):
    """Unit tests for DatabaseManager connection, schema, transactions, and queries."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_name=":memory:")
        self.db.connect()

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_singleton_behavior(self) -> None:
        db1 = DatabaseManager.get_instance()
        db2 = DatabaseManager.get_instance()
        self.assertIs(db1, db2)

    def test_tables_created_on_connect(self) -> None:
        tables = self.db.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('suppliers', 'products')"
        )
        table_names = {row["name"] for row in tables}
        self.assertIn("suppliers", table_names)
        self.assertIn("products", table_names)

    def test_foreign_keys_are_enforced(self) -> None:
        pragma_fk = self.db.execute_query("PRAGMA foreign_keys")
        self.assertEqual(pragma_fk[0][0], 1)

    def test_parameterized_query_execution(self) -> None:
        self.db.execute_query(
            "INSERT INTO suppliers (name, contact_email, phone, address) VALUES (?, ?, ?, ?)",
            ("ACME Supplies", "acme@example.com", "555-0100", "1 Industrial Way"),
        )
        self.assertIsNotNone(self.db.last_inserted_id)

        rows = self.db.execute_query(
            "SELECT * FROM suppliers WHERE name = ?",
            ("ACME Supplies",),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "ACME Supplies")

    def test_transaction_commit(self) -> None:
        self.db.begin_transaction()
        self.db.execute_query(
            "INSERT INTO suppliers (name, contact_email, phone, address) VALUES (?, ?, ?, ?)",
            ("Committed Supplier", "comm@example.com", "555-1111", "2 Road"),
        )
        self.db.commit()

        rows = self.db.execute_query(
            "SELECT * FROM suppliers WHERE name = ?",
            ("Committed Supplier",),
        )
        self.assertEqual(len(rows), 1)

    def test_transaction_rollback(self) -> None:
        self.db.begin_transaction()
        self.db.execute_query(
            "INSERT INTO suppliers (name, contact_email, phone, address) VALUES (?, ?, ?, ?)",
            ("Rollback Supplier", "roll@example.com", "555-2222", "3 Road"),
        )
        self.db.rollback()

        rows = self.db.execute_query(
            "SELECT * FROM suppliers WHERE name = ?",
            ("Rollback Supplier",),
        )
        self.assertEqual(len(rows), 0)

    def test_query_without_connection_raises_error(self) -> None:
        disconnected_db = DatabaseManager(db_name=":memory:")
        with self.assertRaises(ConnectionError):
            disconnected_db.execute_query("SELECT 1")

    def test_concurrent_threads_can_query_shared_db(self) -> None:
        from threading import Thread

        results = []
        errors = []

        def worker(worker_id: int) -> None:
            try:
                rows = self.db.execute_query("SELECT 1 AS val")
                results.append((worker_id, rows[0]["val"]))
            except Exception as e:
                errors.append((worker_id, e))

        threads = [Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent workers encountered errors: {errors}")
        self.assertEqual(len(results), 5)


class TestRepositories(unittest.TestCase):
    """Integration tests for SupplierRepository and ProductRepository."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_name=":memory:")
        self.db.connect()
        self.supplier_repo = SupplierRepository(self.db)
        self.product_repo = ProductRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_supplier_crud(self) -> None:
        # Create
        supplier = Supplier(
            supplier_id=0,
            name="Global Logistics",
            contact_email="info@globallogistics.com",
            phone="800-555-1234",
            address="789 Port St",
        )
        saved = self.supplier_repo.save(supplier)
        self.assertGreater(saved.supplier_id, 0)

        # Read
        fetched = self.supplier_repo.find_by_id(saved.supplier_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.name, "Global Logistics")
        self.assertEqual(fetched.contact_email, "info@globallogistics.com")

        # Update
        fetched.name = "Global Logistics LLC"
        self.supplier_repo.save(fetched)
        updated = self.supplier_repo.find_by_id(saved.supplier_id)
        assert updated is not None
        self.assertEqual(updated.name, "Global Logistics LLC")

        # Find All with filter
        all_suppliers = self.supplier_repo.find_all("Global")
        self.assertEqual(len(all_suppliers), 1)

        # Delete
        self.supplier_repo.delete(saved.supplier_id)
        self.assertIsNone(self.supplier_repo.find_by_id(saved.supplier_id))

        # Delete non-existent raises KeyError
        with self.assertRaises(KeyError):
            self.supplier_repo.delete(saved.supplier_id)

    def test_supplier_delete_referential_integrity(self) -> None:
        supplier = self.supplier_repo.save(
            Supplier(0, "Protected Supplier", "sup@test.com", "555-0000", "Street")
        )
        now = datetime.now()
        self.product_repo.save(
            Product(0, "Item A", "SKU-A", "Parts", 10, 5, Decimal("15.00"), supplier.supplier_id, now)
        )

        # Cannot delete supplier when products reference it
        with self.assertRaises(ValueError):
            self.supplier_repo.delete(supplier.supplier_id)

    def test_product_crud(self) -> None:
        supplier = self.supplier_repo.save(
            Supplier(0, "Tech Distributor", "tech@dist.com", "555-9999", "Tech Park")
        )
        now = datetime.now()

        # Create
        product = Product(
            product_id=0,
            name="USB-C Cable",
            sku="CABLE-01",
            category="Accessories",
            quantity=100,
            reorder_level=20,
            unit_price=Decimal("12.50"),
            supplier_id=supplier.supplier_id,
            updated_at=now,
        )
        saved = self.product_repo.save(product)
        self.assertGreater(saved.product_id, 0)

        # Read
        fetched = self.product_repo.find_by_id(saved.product_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.name, "USB-C Cable")
        self.assertEqual(fetched.sku, "CABLE-01")
        self.assertEqual(fetched.unit_price, Decimal("12.50"))

        # Update
        fetched.quantity = 15  # below reorder level (low stock)
        fetched.unit_price = Decimal("14.00")
        self.product_repo.save(fetched)

        updated = self.product_repo.find_by_id(saved.product_id)
        assert updated is not None
        self.assertEqual(updated.quantity, 15)
        self.assertEqual(updated.unit_price, Decimal("14.00"))

        # Delete
        self.product_repo.delete(saved.product_id)
        self.assertIsNone(self.product_repo.find_by_id(saved.product_id))

        with self.assertRaises(KeyError):
            self.product_repo.delete(saved.product_id)

    def test_product_queries_and_filters(self) -> None:
        sup1 = self.supplier_repo.save(Supplier(0, "Supplier 1", "s1@test.com", "111", "Addr 1"))
        sup2 = self.supplier_repo.save(Supplier(0, "Supplier 2", "s2@test.com", "222", "Addr 2"))
        now = datetime.now()

        # Seed products
        p1 = self.product_repo.save(
            Product(0, "Laptop Pro", "LAP-01", "Computers", 5, 10, Decimal("999.00"), sup1.supplier_id, now)
        )
        p2 = self.product_repo.save(
            Product(0, "Laptop Air", "LAP-02", "Computers", 15, 10, Decimal("799.00"), sup1.supplier_id, now)
        )
        p3 = self.product_repo.save(
            Product(0, "Office Desk", "DSK-01", "Furniture", 2, 5, Decimal("250.00"), sup2.supplier_id, now)
        )
        p4 = self.product_repo.save(
            Product(0, "Desk Lamp", "LMP-01", "Furniture", 50, 10, Decimal("45.00"), sup2.supplier_id, now)
        )

        # Low stock query (p1: 5 <= 10, p3: 2 <= 5)
        low_stock = self.product_repo.find_low_stock()
        low_stock_ids = [p.product_id for p in low_stock]
        self.assertIn(p1.product_id, low_stock_ids)
        self.assertIn(p3.product_id, low_stock_ids)
        self.assertNotIn(p2.product_id, low_stock_ids)
        self.assertNotIn(p4.product_id, low_stock_ids)

        # By supplier
        sup2_products = self.product_repo.find_by_supplier(sup2.supplier_id)
        self.assertEqual(len(sup2_products), 2)
        self.assertEqual({p.product_id for p in sup2_products}, {p3.product_id, p4.product_id})

        # By category filter
        comp_products = self.product_repo.find_all(ProductFilter(category="Computers"))
        self.assertEqual(len(comp_products), 2)

        # By price range filter
        mid_price = self.product_repo.find_all(
            ProductFilter(min_price=Decimal("100"), max_price=Decimal("500"))
        )
        self.assertEqual(len(mid_price), 1)
        self.assertEqual(mid_price[0].product_id, p3.product_id)

        # By search term (name or SKU)
        search_res = self.product_repo.find_all(ProductFilter(search_term="LAP"))
        self.assertEqual(len(search_res), 2)

        search_lamp = self.product_repo.find_all(ProductFilter(search_term="Lamp"))
        self.assertEqual(len(search_lamp), 1)
        self.assertEqual(search_lamp[0].product_id, p4.product_id)

        # Low stock only filter
        filtered_low = self.product_repo.find_all(
            ProductFilter(category="Computers", low_stock_only=True)
        )
        self.assertEqual(len(filtered_low), 1)
        self.assertEqual(filtered_low[0].product_id, p1.product_id)


if __name__ == "__main__":
    unittest.main()
