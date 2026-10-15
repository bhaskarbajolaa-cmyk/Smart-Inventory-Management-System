"""Tests for StockTransaction, Sale, and Purchase transaction models."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from inventory.catalog.models import Product, Supplier
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.suppliers import SupplierRepository
from inventory.transactions.models import Sale, Purchase, TransactionContext


class MockTransactionRepository:
    """Mock transaction repository for testing."""

    def __init__(self) -> None:
        self.transactions: list[Sale | Purchase] = []

    def save(self, transaction: Sale | Purchase) -> Sale | Purchase:
        self.transactions.append(transaction)
        return transaction


class TestSaleTransaction(unittest.TestCase):
    """Unit tests for Sale transaction logic."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_name=":memory:")
        self.db.connect()
        self.supplier_repo = SupplierRepository(self.db)
        self.product_repo = ProductRepository(self.db)
        self.mock_transaction_repo = MockTransactionRepository()

        # Create a supplier and product for testing
        self.supplier = self.supplier_repo.save(
            Supplier(
                supplier_id=0,
                name="Test Supplier",
                contact_email="test@example.com",
                phone="123-456-7890",
                address="123 Test St",
            )
        )

        now = datetime.now(timezone.utc)
        self.product = self.product_repo.save(
            Product(
                product_id=0,
                name="Test Product",
                sku="TEST-001",
                category="Test",
                quantity=100,
                reorder_level=10,
                unit_price=Decimal("29.99"),
                supplier_id=self.supplier.supplier_id,
                updated_at=now,
            )
        )

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_sale_execute_success(self) -> None:
        """Test that a successful sale decreases stock and updates status."""
        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )

        sale = Sale(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            sale_price=Decimal("29.99"),
            customer_ref="CUST-001",
            sold_by_user_id=1,
        )

        # Execute the sale
        result = sale.execute(context)

        # Verify execution succeeded
        self.assertTrue(result)
        # Status should be "completed" after a successful sale that is persisted
        self.assertEqual(sale.status, "completed")

        # Verify the transaction was recorded in the repository
        self.assertEqual(len(context.transaction_repository.transactions), 1)

        # Verify stock was decreased
        updated_product = self.product_repo.find_by_id(self.product.product_id)
        self.assertIsNotNone(updated_product)
        assert updated_product is not None
        self.assertEqual(updated_product.quantity, 90)  # 100 - 10

    def test_sale_execute_insufficient_stock(self) -> None:
        """Test that a sale fails when there's insufficient stock."""
        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )

        sale = Sale(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=150,  # More than available (100)
            timestamp=datetime.now(timezone.utc),
            status="pending",
            sale_price=Decimal("29.99"),
            customer_ref="CUST-001",
            sold_by_user_id=1,
        )

        # Execute the sale should fail
        with self.assertRaises(ValueError) as context_manager:
            sale.execute(context)

        self.assertIn("Insufficient stock", str(context_manager.exception))
        self.assertEqual(sale.status, "failed")

        # Verify stock was NOT changed
        updated_product = self.product_repo.find_by_id(self.product.product_id)
        self.assertIsNotNone(updated_product)
        assert updated_product is not None
        self.assertEqual(updated_product.quantity, 100)  # Still 100

    def test_sale_execute_nonexistent_product(self) -> None:
        """Test that a sale fails when the product doesn't exist."""
        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )

        sale = Sale(
            transaction_id=0,
            product_id=99999,  # Non-existent product
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            sale_price=Decimal("29.99"),
            customer_ref="CUST-001",
            sold_by_user_id=1,
        )

        # Execute the sale should fail
        with self.assertRaises(LookupError) as context_manager:
            sale.execute(context)

        self.assertIn("does not exist", str(context_manager.exception))
        self.assertEqual(sale.status, "failed")

    def test_sale_calculate_total(self) -> None:
        """Test that sale total calculation is correct."""
        sale = Sale(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=5,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            sale_price=Decimal("29.99"),
            customer_ref="CUST-001",
            sold_by_user_id=1,
        )

        total = sale.calculate_total()
        expected_total = Decimal("29.99") * Decimal(5)
        self.assertEqual(total, expected_total)

    def test_sale_rollback(self) -> None:
        """Test that sale rollback updates status correctly."""
        sale = Sale(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="completed",
            sale_price=Decimal("29.99"),
            customer_ref="CUST-001",
            sold_by_user_id=1,
        )

        sale.rollback()
        self.assertEqual(sale.status, "rolled_back")

    def test_sale_log_transaction(self) -> None:
        """Test that sale logging persists to the repository and sets status to completed."""
        sale = Sale(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            sale_price=Decimal("29.99"),
            customer_ref="CUST-001",
            sold_by_user_id=1,
        )

        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )
        sale.log_transaction(context)
        self.assertEqual(sale.status, "completed")
        self.assertEqual(len(self.mock_transaction_repo.transactions), 1)


class TestPurchaseTransaction(unittest.TestCase):
    """Unit tests for Purchase transaction logic."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_name=":memory:")
        self.db.connect()
        self.supplier_repo = SupplierRepository(self.db)
        self.product_repo = ProductRepository(self.db)
        self.mock_transaction_repo = MockTransactionRepository()

        # Create a supplier and product for testing
        self.supplier = self.supplier_repo.save(
            Supplier(
                supplier_id=0,
                name="Test Supplier",
                contact_email="test@example.com",
                phone="123-456-7890",
                address="123 Test St",
            )
        )

        now = datetime.now(timezone.utc)
        self.product = self.product_repo.save(
            Product(
                product_id=0,
                name="Test Product",
                sku="TEST-002",
                category="Test",
                quantity=50,
                reorder_level=5,
                unit_price=Decimal("19.99"),
                supplier_id=self.supplier.supplier_id,
                updated_at=now,
            )
        )

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_purchase_execute_success(self) -> None:
        """Test that a successful purchase increases stock and updates status."""
        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )

        purchase = Purchase(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=25,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            unit_cost=Decimal("10.00"),
            supplier_id=self.supplier.supplier_id,
            ordered_by_user_id=1,
        )

        result = purchase.execute(context)

        self.assertTrue(result)
        self.assertEqual(purchase.status, "completed")

        # Verify the transaction was recorded in the repository
        self.assertEqual(len(self.mock_transaction_repo.transactions), 1)

        # Verify stock was increased
        updated_product = self.product_repo.find_by_id(self.product.product_id)
        self.assertIsNotNone(updated_product)
        assert updated_product is not None
        self.assertEqual(updated_product.quantity, 75)  # 50 + 25

    def test_purchase_execute_nonexistent_product(self) -> None:
        """Test that a purchase fails when the product doesn't exist."""
        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )

        purchase = Purchase(
            transaction_id=0,
            product_id=99999,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            unit_cost=Decimal("10.00"),
            supplier_id=1,
            ordered_by_user_id=1,
        )

        with self.assertRaises(LookupError) as context_manager:
            purchase.execute(context)

        self.assertIn("does not exist", str(context_manager.exception))
        self.assertEqual(purchase.status, "failed")

    def test_purchase_calculate_total(self) -> None:
        """Test that purchase total calculation is correct."""
        purchase = Purchase(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            unit_cost=Decimal("10.00"),
            supplier_id=1,
            ordered_by_user_id=1,
        )

        total = purchase.calculate_total()
        self.assertEqual(total, Decimal("100.00"))

    def test_purchase_rollback(self) -> None:
        """Test that purchase rollback updates status correctly."""
        purchase = Purchase(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="completed",
            unit_cost=Decimal("10.00"),
            supplier_id=1,
            ordered_by_user_id=1,
        )

        purchase.rollback()
        self.assertEqual(purchase.status, "rolled_back")

    def test_purchase_log_transaction(self) -> None:
        """Test that purchase logging persists to the repository and sets status to completed."""
        purchase = Purchase(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=10,
            timestamp=datetime.now(timezone.utc),
            status="pending",
            unit_cost=Decimal("10.00"),
            supplier_id=1,
            ordered_by_user_id=1,
        )

        context = TransactionContext(
            product_repository=self.product_repo,
            transaction_repository=self.mock_transaction_repo,
            database_manager=self.db,
        )
        purchase.log_transaction(context)
        self.assertEqual(purchase.status, "completed")
        self.assertEqual(len(self.mock_transaction_repo.transactions), 1)


if __name__ == "__main__":
    unittest.main()

