"""Stock transaction entities and their shared abstract behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inventory.persistence.database import DatabaseManager
    from inventory.persistence.repositories.products import ProductRepository
    from inventory.persistence.repositories.transactions import StockTransactionRepository


@dataclass
class TransactionContext:
    """Explicit collaborators used by stock transactions; entities stay persistence-free."""

    product_repository: ProductRepository
    transaction_repository: StockTransactionRepository
    database_manager: DatabaseManager


@dataclass
class StockTransaction(ABC):
    """Abstract base for operations that change inventory stock."""

    transaction_id: int
    product_id: int
    user_id: int
    quantity: int
    timestamp: datetime
    status: str = "pending"

    # Internal state — set during execute() for use by rollback()
    _original_quantity: int = field(init=False, repr=False, default=0)
    _product: Any = field(init=False, repr=False, default=None)

    @abstractmethod
    def execute(self, context: TransactionContext) -> bool:
        """Execute this stock change using the provided collaborators."""
        ...

    def rollback(self) -> None:
        """Restore the in-memory product to its pre-execute quantity and set status.

        The SQL-level undo is handled by ``context.database_manager.rollback()``
        inside ``execute()``.  This method only repairs the in-memory ``Product``
        object that was modified before the failure, so callers holding a
        reference to it see a consistent state.
        """
        self.status = "rolled_back"
        if self._product is not None and self._original_quantity > 0:
            self._product.quantity = self._original_quantity

    @abstractmethod
    def log_transaction(self, context: TransactionContext) -> None:
        """Persist this transaction to ``stock_transactions`` and mark it completed."""
        ...


@dataclass
class Sale(StockTransaction):
    """Sale transaction that decreases product stock atomically."""

    sale_price: Decimal = field(default_factory=lambda: Decimal("0.00"))
    customer_ref: str = ""
    sold_by_user_id: int = 0

    def execute(self, context: TransactionContext) -> bool:
        """Check stock, decrement via Product.update_stock, save, and commit atomically."""
        try:
            context.database_manager.begin_transaction()

            product = context.product_repository.find_by_id(self.product_id)
            if product is None:
                context.database_manager.rollback()
                raise LookupError(f"Product {self.product_id} does not exist.")

            # Store original quantity so rollback() can restore the in-memory object
            self._original_quantity = product.quantity
            self._product = product

            if product.quantity < self.quantity:
                context.database_manager.rollback()
                raise ValueError(
                    f"Insufficient stock. "
                    f"Available: {product.quantity}, Requested: {self.quantity}"
                )

            product.update_stock(-self.quantity)
            context.product_repository.save(product)

            # Persist the transaction row and mark status = "completed"
            self.log_transaction(context)

            context.database_manager.commit()
            return True

        except Exception as error:
            context.database_manager.rollback()
            # Restore in-memory product state so the object is not left in a
            # partially-modified state after a failed execute.
            if self._product is not None and self._original_quantity > 0:
                self._product.quantity = self._original_quantity
            self.status = "failed"
            raise error

    def calculate_total(self) -> Decimal:
        """Multiply the sold quantity by the sale price."""
        return self.sale_price * Decimal(self.quantity)

    def rollback(self) -> None:
        """Restore the original stock quantity after a failure."""
        super().rollback()

    def log_transaction(self, context: TransactionContext) -> None:
        """Persist the sale to ``stock_transactions`` and set status to ``"completed"``."""
        context.transaction_repository.save(self)
        self.status = "completed"


@dataclass
class Purchase(StockTransaction):
    """Purchase transaction that increases product stock."""

    unit_cost: Decimal = field(default_factory=lambda: Decimal("0.00"))
    supplier_id: int = 0
    ordered_by_user_id: int = 0

    def execute(self, context: TransactionContext) -> bool:
        """Increase stock, save the purchase, and commit atomically."""
        try:
            context.database_manager.begin_transaction()

            product = context.product_repository.find_by_id(self.product_id)
            if product is None:
                context.database_manager.rollback()
                raise LookupError(f"Product {self.product_id} does not exist.")

            self._original_quantity = product.quantity
            self._product = product

            product.update_stock(self.quantity)
            context.product_repository.save(product)

            # Persist the transaction row and mark status = "completed"
            self.log_transaction(context)

            context.database_manager.commit()
            return True

        except Exception as error:
            context.database_manager.rollback()
            if self._product is not None and self._original_quantity > 0:
                self._product.quantity = self._original_quantity
            self.status = "failed"
            raise error

    def calculate_total(self) -> Decimal:
        """Multiply the purchased quantity by the unit cost."""
        return self.unit_cost * Decimal(self.quantity)

    def rollback(self) -> None:
        """Restore the original stock quantity after a failure."""
        super().rollback()

    def log_transaction(self, context: TransactionContext) -> None:
        """Persist the purchase to ``stock_transactions`` and set status to ``"completed"``."""
        context.transaction_repository.save(self)
        self.status = "completed"
