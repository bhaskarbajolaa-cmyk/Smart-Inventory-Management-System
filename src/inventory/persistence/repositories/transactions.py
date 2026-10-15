"""Persistence operations for sales, purchases, and stock transactions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository
from inventory.transactions.models import Purchase, Sale, StockTransaction


class StockTransactionRepository(Repository[StockTransaction, object]):
    """Transaction storage and product-based lookup operations."""

    def __init__(self, db: DatabaseManager) -> None:
        super().__init__(db)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_transaction(self, row: Any) -> StockTransaction:
        """Reconstruct a Sale or Purchase from a ``stock_transactions`` row."""
        ts = row["timestamp"]
        timestamp = datetime.fromisoformat(ts) if isinstance(ts, str) else ts

        txn_type = row["transaction_type"]

        if txn_type == "sale":
            return Sale(
                transaction_id=int(row["transaction_id"]),
                product_id=int(row["product_id"]),
                user_id=int(row["user_id"]),
                quantity=int(row["quantity"]),
                timestamp=timestamp,
                status=str(row["status"]),
                sale_price=(
                    Decimal(str(row["sale_price"]))
                    if row["sale_price"] is not None
                    else Decimal("0.00")
                ),
                customer_ref=str(row["customer_ref"]) if row["customer_ref"] else "",
                sold_by_user_id=(
                    int(row["sold_by_user_id"]) if row["sold_by_user_id"] is not None else 0
                ),
            )

        # txn_type == "purchase"
        return Purchase(
            transaction_id=int(row["transaction_id"]),
            product_id=int(row["product_id"]),
            user_id=int(row["user_id"]),
            quantity=int(row["quantity"]),
            timestamp=timestamp,
            status=str(row["status"]),
            unit_cost=(
                Decimal(str(row["unit_cost"]))
                if row["unit_cost"] is not None
                else Decimal("0.00")
            ),
            supplier_id=int(row["supplier_id"]) if row["supplier_id"] is not None else 0,
            ordered_by_user_id=(
                int(row["ordered_by_user_id"])
                if row["ordered_by_user_id"] is not None
                else 0
            ),
        )

    # ------------------------------------------------------------------
    # Repository contract
    # ------------------------------------------------------------------

    def find_by_id(self, entity_id: int) -> StockTransaction | None:
        """Fetch and map one transaction row by transaction ID."""
        sql = (
            "SELECT transaction_id, product_id, user_id, quantity, timestamp, "
            "status, transaction_type, sale_price, customer_ref, sold_by_user_id, "
            "unit_cost, supplier_id, ordered_by_user_id "
            "FROM stock_transactions WHERE transaction_id = ?"
        )
        rows = self.db.execute_query(sql, (entity_id,))
        if not rows:
            return None
        return self._row_to_transaction(rows[0])

    def find_all(self, filters: object | None = None) -> list[StockTransaction]:
        """Return all transactions, optionally filtered by transaction type string."""
        sql = (
            "SELECT transaction_id, product_id, user_id, quantity, timestamp, "
            "status, transaction_type, sale_price, customer_ref, sold_by_user_id, "
            "unit_cost, supplier_id, ordered_by_user_id "
            "FROM stock_transactions"
        )
        conditions: list[str] = []
        params: list[Any] = []

        if isinstance(filters, str) and filters in ("sale", "purchase"):
            conditions.append("transaction_type = ?")
            params.append(filters)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY transaction_id ASC"

        rows = self.db.execute_query(sql, params if params else None)
        return [self._row_to_transaction(row) for row in rows]

    def save(self, entity: StockTransaction) -> StockTransaction:
        """Insert a new sale or purchase row into ``stock_transactions``.

        Updates are not supported for audit-retention reasons — transactions
        are immutable records once created.
        """
        timestamp_str = (
            entity.timestamp.isoformat()
            if isinstance(entity.timestamp, datetime)
            else str(entity.timestamp)
        )

        if isinstance(entity, Sale):
            txn_type = "sale"
            sale_price = str(entity.sale_price)
            customer_ref = entity.customer_ref
            sold_by_user_id: int | None = entity.sold_by_user_id
            unit_cost = None
            supplier_ref: int | None = None
            ordered_by: int | None = None
        elif isinstance(entity, Purchase):
            txn_type = "purchase"
            sale_price = None
            customer_ref = None
            sold_by_user_id = None
            unit_cost = str(entity.unit_cost)
            supplier_ref = entity.supplier_id
            ordered_by = entity.ordered_by_user_id
        else:
            raise TypeError(
                f"Cannot save unknown transaction subtype: {type(entity).__name__}"
            )

        sql = (
            "INSERT INTO stock_transactions "
            "(product_id, user_id, quantity, timestamp, status, transaction_type, "
            "sale_price, customer_ref, sold_by_user_id, unit_cost, supplier_id, ordered_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING transaction_id"
        )
        params_tuple = (
            entity.product_id,
            entity.user_id,
            entity.quantity,
            timestamp_str,
            entity.status,
            txn_type,
            sale_price,
            customer_ref,
            sold_by_user_id,
            unit_cost,
            supplier_ref,
            ordered_by,
        )
        rows = self.db.execute_query(sql, params_tuple)
        if rows:
            entity.transaction_id = int(rows[0]["transaction_id"])
        elif self.db.last_inserted_id:
            entity.transaction_id = self.db.last_inserted_id
        return entity

    def delete(self, entity_id: int) -> None:
        # TODO: Delete a transaction only when audit-retention rules allow it.
        raise NotImplementedError

    def find_by_product(self, product_id: int) -> list[StockTransaction]:
        """Return the full transaction history for one product, oldest first."""
        sql = (
            "SELECT transaction_id, product_id, user_id, quantity, timestamp, "
            "status, transaction_type, sale_price, customer_ref, sold_by_user_id, "
            "unit_cost, supplier_id, ordered_by_user_id "
            "FROM stock_transactions WHERE product_id = ? "
            "ORDER BY transaction_id ASC"
        )
        rows = self.db.execute_query(sql, (product_id,))
        return [self._row_to_transaction(row) for row in rows]
