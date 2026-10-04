"""Stock transaction entities and their shared abstract behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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
    status: str

    @abstractmethod
    def execute(self, context: TransactionContext) -> bool:
        # TODO: Perform the stock change inside a transaction and report success.
        raise NotImplementedError

    @abstractmethod
    def rollback(self) -> None:
        # TODO: Restore transaction state and roll back persistence after failure.
        raise NotImplementedError

    @abstractmethod
    def log_transaction(self) -> None:
        # TODO: Persist an audit record for this stock operation.
        raise NotImplementedError


@dataclass
class Sale(StockTransaction):
    """Sale transaction that decreases product stock atomically."""

    sale_price: Decimal
    customer_ref: str
    sold_by_user_id: int

    def execute(self, context: TransactionContext) -> bool:
        # TODO: Check stock, decrement via Product.update_stock, save, and commit atomically.
        raise NotImplementedError

    def calculate_total(self) -> Decimal:
        # TODO: Multiply the sold quantity by the sale price.
        raise NotImplementedError

    def rollback(self) -> None:
        # TODO: Delegate to the active transaction and restore any in-memory changes.
        raise NotImplementedError

    def log_transaction(self) -> None:
        # TODO: Record the sale outcome and actor for auditing.
        raise NotImplementedError


@dataclass
class Purchase(StockTransaction):
    """Purchase transaction that increases product stock."""

    unit_cost: Decimal
    supplier_id: int
    ordered_by_user_id: int

    def execute(self, context: TransactionContext) -> bool:
        # TODO: Increase stock, save the purchase, and commit atomically.
        raise NotImplementedError

    def calculate_total(self) -> Decimal:
        # TODO: Multiply the purchased quantity by the unit cost.
        raise NotImplementedError

    def rollback(self) -> None:
        # TODO: Delegate to the active transaction and restore any in-memory changes.
        raise NotImplementedError

    def log_transaction(self) -> None:
        # TODO: Record the purchase outcome and actor for auditing.
        raise NotImplementedError
