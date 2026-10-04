"""Persistence operations for products."""

from inventory.catalog.filters import ProductFilter
from inventory.catalog.models import Product
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repository import Repository


class ProductRepository(Repository[Product, ProductFilter]):
    """Product queries, including low-stock and supplier filters."""

    def __init__(self, db: DatabaseManager) -> None:
        # TODO: Initialize the base repository with the shared database manager.
        super().__init__(db)

    def find_by_id(self, entity_id: int) -> Product | None:
        # TODO: Fetch and map one product row by product ID.
        raise NotImplementedError

    def find_all(self, filters: ProductFilter | None = None) -> list[Product]:
        # TODO: Apply every supplied ProductFilter field and map matching rows.
        raise NotImplementedError

    def save(self, entity: Product) -> Product:
        # TODO: Insert or update a product using parameterized DB operations.
        raise NotImplementedError

    def delete(self, entity_id: int) -> None:
        # TODO: Delete the product and define how related records are handled.
        raise NotImplementedError

    def find_low_stock(self) -> list[Product]:
        # TODO: Return products whose quantities are at or below reorder levels.
        raise NotImplementedError

    def find_by_supplier(self, supplier_id: int) -> list[Product]:
        # TODO: Return every product supplied by the requested supplier.
        raise NotImplementedError
