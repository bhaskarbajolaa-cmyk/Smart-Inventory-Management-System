"""Search and filter value objects for catalog queries."""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.catalog.models import Product


@dataclass
class ProductFilter:
    """Optional product search criteria for repository queries."""

    category: str | None = None
    supplier_id: int | None = None
    low_stock_only: bool = False
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    search_term: str | None = None

    def matches(self, product: "Product") -> bool:
        """Check if an in-memory product satisfies all active filter criteria."""
        if self.category is not None and product.category.lower() != self.category.lower():
            return False
        if self.supplier_id is not None and product.supplier_id != self.supplier_id:
            return False
        if self.low_stock_only and not product.is_low_stock():
            return False
        if self.min_price is not None and product.unit_price < self.min_price:
            return False
        if self.max_price is not None and product.unit_price > self.max_price:
            return False
        if self.search_term is not None:
            term = self.search_term.lower()
            if term not in product.name.lower() and term not in product.sku.lower():
                return False
        return True
