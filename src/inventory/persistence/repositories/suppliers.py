"""Persistence operations for suppliers."""

from inventory.catalog.models import Supplier
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class SupplierRepository(Repository[Supplier, object]):
    """Supplier storage and lookup operations."""

    def __init__(self, db: DatabaseManager) -> None:
        # TODO: Initialize the base repository with the shared database manager.
        super().__init__(db)

    def find_by_id(self, entity_id: int) -> Supplier | None:
        # TODO: Fetch and map one supplier row by supplier ID.
        raise NotImplementedError

    def find_all(self, filters: object | None = None) -> list[Supplier]:
        # TODO: Return supplier rows, applying supported filters if provided.
        raise NotImplementedError

    def save(self, entity: Supplier) -> Supplier:
        # TODO: Insert or update a supplier using parameterized DB operations.
        raise NotImplementedError

    def delete(self, entity_id: int) -> None:
        # TODO: Delete the supplier and preserve product referential integrity.
        raise NotImplementedError
