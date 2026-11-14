"""Cache-aware product repository that preserves synchronous stock writes."""

from __future__ import annotations

import threading
import uuid

from inventory.cache.inventory_cache import InventoryCache
from inventory.catalog.filters import ProductFilter
from inventory.catalog.models import Product
from inventory.concurrency.locks import LockManager
from inventory.persistence.repositories.products import ProductRepository


class CachedProductRepository:
    """Apply cache-aside reads and defer only product name/price edits."""

    def __init__(
        self,
        repository: ProductRepository,
        cache: InventoryCache,
        lock_manager: LockManager,
    ) -> None:
        self.repository = repository
        self.cache = cache
        self.lock_manager = lock_manager
        self._request_state = threading.local()

    @property
    def last_cache_status(self) -> str:
        return getattr(self._request_state, "cache_status", "MISS")

    def find_by_id(self, entity_id: int) -> Product | None:
        product = self.cache.get_product(entity_id)
        if product is not None:
            self._request_state.cache_status = "HIT"
            return product

        self._request_state.cache_status = "MISS"
        product = self.repository.find_by_id(entity_id)
        if product is None:
            return None
        self.cache.apply_pending_metadata(product)
        self.cache.put_product(product)
        return product

    def count(self, filters: ProductFilter | None = None) -> int:
        return self.repository.count(filters)

    def find_all(self, filters: ProductFilter | None = None) -> list[Product]:
        if filters is not None and (filters.limit is not None or filters.offset is not None):
            res_products = self.repository.find_all(filters)
            for product in res_products:
                self.cache.apply_pending_metadata(product)
                self.cache.put_product(product)
            self._request_state.cache_status = "MISS"
            return res_products

        products = {
            product.product_id: product
            for product in self.repository.find_all(filters)
        }

        for key in self.cache.get_dirty_keys():
            prefix, separator, raw_id = key.partition(":")
            if prefix != "product" or not separator:
                continue
            try:
                product_id = int(raw_id)
            except ValueError:
                continue

            product = products.get(product_id)
            if product is None:
                product = self.repository.find_by_id(product_id)
            if product is None:
                continue

            self.cache.apply_pending_metadata(product)
            if filters is not None and not filters.matches(product):
                products.pop(product_id, None)
            else:
                products[product_id] = product

        for product in products.values():
            self.cache.apply_pending_metadata(product)
            self.cache.put_product(product)
        self._request_state.cache_status = "MISS"
        return [products[key] for key in sorted(products)]

    def save(self, entity: Product) -> Product:
        if entity.product_id <= 0:
            saved = self.repository.save(entity)
            self.cache.put_product(saved)
            return saved

        token = self.lock_manager.acquire(
            str(entity.product_id),
            owner_id=f"product-edit:{uuid.uuid4()}",
        )
        if token is None:
            raise TimeoutError(f"Could not acquire product lock for {entity.product_id}.")

        key = f"product:{entity.product_id}"
        try:
            self.cache.put_product(entity, dirty=True)
            try:
                self.repository.save_operational_fields(entity)
            except Exception:
                self.cache.invalidate_product(entity.product_id)
                self.cache.mark_clean(key)
                raise
        finally:
            self.lock_manager.release(token)
        return entity

    def delete(self, entity_id: int) -> None:
        token = self.lock_manager.acquire(
            str(entity_id),
            owner_id=f"product-delete:{uuid.uuid4()}",
        )
        if token is None:
            raise TimeoutError(f"Could not acquire product lock for {entity_id}.")
        try:
            self.cache.invalidate_product(entity_id)
            self.repository.delete(entity_id)
            self.cache.remove_product(entity_id)
        finally:
            self.lock_manager.release(token)
