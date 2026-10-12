from typing import Any

from inventory.catalog.models import Supplier
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class SupplierRepository(Repository[Supplier, object]):
    """Supplier storage and lookup operations."""

    def __init__(self, db: DatabaseManager) -> None:
        """Initialize the base repository with the shared database manager."""
        super().__init__(db)

    def _row_to_supplier(self, row: Any) -> Supplier:
        return Supplier(
            supplier_id=int(row["supplier_id"]),
            name=str(row["name"]),
            contact_email=str(row["contact_email"]),
            phone=str(row["phone"]),
            address=str(row["address"]),
        )

    def find_by_id(self, entity_id: int) -> Supplier | None:
        """Fetch and map one supplier row by supplier ID."""
        sql = (
            "SELECT supplier_id, name, contact_email, phone, address "
            "FROM suppliers WHERE supplier_id = ?"
        )
        rows = self.db.execute_query(sql, (entity_id,))
        if not rows:
            return None
        return self._row_to_supplier(rows[0])

    def find_all(self, filters: object | None = None) -> list[Supplier]:
        """Return supplier rows, applying supported filters if provided."""
        sql = "SELECT supplier_id, name, contact_email, phone, address FROM suppliers"
        params: list[Any] = []
        conditions: list[str] = []

        if isinstance(filters, str) and filters.strip():
            conditions.append("name LIKE ?")
            params.append(f"%{filters.strip()}%")
        elif isinstance(filters, dict):
            if "name" in filters and filters["name"]:
                conditions.append("name LIKE ?")
                params.append(f"%{filters['name']}%")
            if "contact_email" in filters and filters["contact_email"]:
                conditions.append("contact_email = ?")
                params.append(filters["contact_email"])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY supplier_id ASC"
        rows = self.db.execute_query(sql, params)
        return [self._row_to_supplier(row) for row in rows]

    def save(self, entity: Supplier) -> Supplier:
        """Insert or update a supplier using parameterized DB operations."""
        if entity.supplier_id and self.find_by_id(entity.supplier_id) is not None:
            update_sql = (
                "UPDATE suppliers "
                "SET name = ?, contact_email = ?, phone = ?, address = ? "
                "WHERE supplier_id = ?"
            )
            update_params: tuple[Any, ...] = (
                entity.name,
                entity.contact_email,
                entity.phone,
                entity.address,
                entity.supplier_id,
            )
            self.db.execute_query(update_sql, update_params)
            return entity
        elif entity.supplier_id and entity.supplier_id > 0:
            insert_sql = (
                "INSERT INTO suppliers (supplier_id, name, contact_email, phone, address) "
                "VALUES (?, ?, ?, ?, ?)"
            )
            insert_params: tuple[Any, ...] = (
                entity.supplier_id,
                entity.name,
                entity.contact_email,
                entity.phone,
                entity.address,
            )
            self.db.execute_query(insert_sql, insert_params)
            return entity
        else:
            auto_sql = (
                "INSERT INTO suppliers (name, contact_email, phone, address) "
                "VALUES (?, ?, ?, ?) RETURNING supplier_id"
            )
            auto_params: tuple[Any, ...] = (
                entity.name,
                entity.contact_email,
                entity.phone,
                entity.address,
            )
            rows = self.db.execute_query(auto_sql, auto_params)
            if rows:
                entity.supplier_id = int(rows[0]["supplier_id"])
            elif self.db.last_inserted_id:
                entity.supplier_id = self.db.last_inserted_id
            return entity

    def delete(self, entity_id: int) -> None:
        """Delete the supplier and preserve product referential integrity."""
        supplier = self.find_by_id(entity_id)
        if supplier is None:
            raise KeyError(f"Supplier with ID {entity_id} does not exist.")

        check_sql = "SELECT COUNT(*) AS count FROM products WHERE supplier_id = ?"
        rows = self.db.execute_query(check_sql, (entity_id,))
        count = int(rows[0]["count"]) if rows else 0
        if count > 0:
            raise ValueError(
                f"Cannot delete supplier {entity_id}: {count} products still reference this supplier."
            )

        sql = "DELETE FROM suppliers WHERE supplier_id = ?"
        self.db.execute_query(sql, (entity_id,))
