from datetime import datetime
from decimal import Decimal
from typing import Any

from inventory.catalog.filters import ProductFilter
from inventory.catalog.models import Product
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class ProductRepository(Repository[Product, ProductFilter]):
    """Product queries, including low-stock and supplier filters."""

    def __init__(self, db: DatabaseManager) -> None:
        """Initialize the base repository with the shared database manager."""
        super().__init__(db)

    def _row_to_product(self, row: Any) -> Product:
        raw_updated_at = row["updated_at"]
        if isinstance(raw_updated_at, str):
            updated_at = datetime.fromisoformat(raw_updated_at)
        elif isinstance(raw_updated_at, datetime):
            updated_at = raw_updated_at
        else:
            updated_at = datetime.now()

        return Product(
            product_id=int(row["product_id"]),
            name=str(row["name"]),
            sku=str(row["sku"]),
            category=str(row["category"]),
            quantity=int(row["quantity"]),
            reorder_level=int(row["reorder_level"]),
            unit_price=Decimal(str(row["unit_price"])),
            supplier_id=int(row["supplier_id"]),
            updated_at=updated_at,
        )

    def find_by_id(self, entity_id: int) -> Product | None:
        """Fetch and map one product row by product ID."""
        sql = (
            "SELECT product_id, name, sku, category, quantity, reorder_level, "
            "unit_price, supplier_id, updated_at "
            "FROM products WHERE product_id = ?"
        )
        rows = self.db.execute_query(sql, (entity_id,))
        if not rows:
            return None
        return self._row_to_product(rows[0])

    def find_all(self, filters: ProductFilter | None = None) -> list[Product]:
        """Apply every supplied ProductFilter field and map matching rows."""
        sql = (
            "SELECT product_id, name, sku, category, quantity, reorder_level, "
            "unit_price, supplier_id, updated_at "
            "FROM products"
        )
        conditions: list[str] = []
        params: list[Any] = []

        if filters is not None:
            if filters.category is not None:
                conditions.append("LOWER(category) = LOWER(?)")
                params.append(filters.category)
            if filters.supplier_id is not None:
                conditions.append("supplier_id = ?")
                params.append(filters.supplier_id)
            if filters.low_stock_only:
                conditions.append("quantity <= reorder_level")
            if filters.min_price is not None:
                conditions.append("CAST(unit_price AS REAL) >= ?")
                params.append(float(filters.min_price))
            if filters.max_price is not None:
                conditions.append("CAST(unit_price AS REAL) <= ?")
                params.append(float(filters.max_price))
            if filters.search_term is not None and filters.search_term.strip():
                term = f"%{filters.search_term.strip()}%"
                conditions.append("(name LIKE ? OR sku LIKE ?)")
                params.extend([term, term])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY product_id ASC"
        rows = self.db.execute_query(sql, params)
        return [self._row_to_product(row) for row in rows]

    def find_low_stock(self) -> list[Product]:
        """Return products whose quantities are at or below reorder levels."""
        sql = (
            "SELECT product_id, name, sku, category, quantity, reorder_level, "
            "unit_price, supplier_id, updated_at "
            "FROM products WHERE quantity <= reorder_level ORDER BY product_id ASC"
        )
        rows = self.db.execute_query(sql)
        return [self._row_to_product(row) for row in rows]

    def find_by_supplier(self, supplier_id: int) -> list[Product]:
        """Return every product supplied by the requested supplier."""
        sql = (
            "SELECT product_id, name, sku, category, quantity, reorder_level, "
            "unit_price, supplier_id, updated_at "
            "FROM products WHERE supplier_id = ? ORDER BY product_id ASC"
        )
        rows = self.db.execute_query(sql, (supplier_id,))
        return [self._row_to_product(row) for row in rows]

    def save(self, entity: Product) -> Product:
        """Insert or update a product using parameterized DB operations."""
        updated_at_str = (
            entity.updated_at.isoformat()
            if isinstance(entity.updated_at, datetime)
            else str(entity.updated_at)
        )
        unit_price_str = str(entity.unit_price)

        if entity.product_id and self.find_by_id(entity.product_id) is not None:
            update_sql = (
                "UPDATE products "
                "SET name = ?, sku = ?, category = ?, quantity = ?, "
                "reorder_level = ?, unit_price = ?, supplier_id = ?, updated_at = ? "
                "WHERE product_id = ?"
            )
            update_params: tuple[Any, ...] = (
                entity.name,
                entity.sku,
                entity.category,
                entity.quantity,
                entity.reorder_level,
                unit_price_str,
                entity.supplier_id,
                updated_at_str,
                entity.product_id,
            )
            self.db.execute_query(update_sql, update_params)
            return entity
        elif entity.product_id and entity.product_id > 0:
            insert_sql = (
                "INSERT INTO products (product_id, name, sku, category, quantity, "
                "reorder_level, unit_price, supplier_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            insert_params: tuple[Any, ...] = (
                entity.product_id,
                entity.name,
                entity.sku,
                entity.category,
                entity.quantity,
                entity.reorder_level,
                unit_price_str,
                entity.supplier_id,
                updated_at_str,
            )
            self.db.execute_query(insert_sql, insert_params)
            return entity
        else:
            auto_sql = (
                "INSERT INTO products (name, sku, category, quantity, "
                "reorder_level, unit_price, supplier_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING product_id"
            )
            auto_params: tuple[Any, ...] = (
                entity.name,
                entity.sku,
                entity.category,
                entity.quantity,
                entity.reorder_level,
                unit_price_str,
                entity.supplier_id,
                updated_at_str,
            )
            rows = self.db.execute_query(auto_sql, auto_params)
            if rows:
                entity.product_id = int(rows[0]["product_id"])
            elif self.db.last_inserted_id:
                entity.product_id = self.db.last_inserted_id
            return entity

    def delete(self, entity_id: int) -> None:
        """Delete the product and define how related records are handled."""
        product = self.find_by_id(entity_id)
        if product is None:
            raise KeyError(f"Product with ID {entity_id} does not exist.")

        check_sql = "SELECT COUNT(*) AS count FROM stock_transactions WHERE product_id = ?"
        rows = self.db.execute_query(check_sql, (entity_id,))
        count = int(rows[0]["count"]) if rows else 0
        if count > 0:
            raise ValueError(
                f"Cannot delete product {entity_id}: {count} stock transactions are associated with this product."
            )

        sql = "DELETE FROM products WHERE product_id = ?"
        self.db.execute_query(sql, (entity_id,))
