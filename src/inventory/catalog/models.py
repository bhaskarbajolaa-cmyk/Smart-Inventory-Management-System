"""Product and supplier entities; persistence belongs in repositories."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class Product:
    """Inventory item with stock update and low-stock rules."""

    product_id: int
    name: str
    sku: str
    category: str
    quantity: int
    reorder_level: int
    unit_price: Decimal
    supplier_id: int
    updated_at: datetime

    def update_stock(self, quantity_delta: int) -> None:
        # TODO: Apply a stock change and reject quantities below zero.
        raise NotImplementedError

    def is_low_stock(self) -> bool:
        # TODO: Compare current quantity with the configured reorder level.
        raise NotImplementedError


@dataclass
class Supplier:
    """Supplier contact and identity data."""

    supplier_id: int
    name: str
    contact_email: str
    phone: str
    address: str
