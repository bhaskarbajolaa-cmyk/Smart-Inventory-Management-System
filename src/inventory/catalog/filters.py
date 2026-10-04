"""Search and filter value objects for catalog queries."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class ProductFilter:
    """Optional product search criteria for repository queries."""

    category: str | None = None
    supplier_id: int | None = None
    low_stock_only: bool = False
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    search_term: str | None = None
