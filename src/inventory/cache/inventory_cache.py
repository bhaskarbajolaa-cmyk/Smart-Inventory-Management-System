"""Product cache coordination, dirty metadata buffering, and cache statistics."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from decimal import Decimal
from time import monotonic
from typing import Any

from inventory.cache.client import CacheClient
from inventory.cache.eviction import LFUPolicy, LRUPolicy
from inventory.catalog.models import Product


class InventoryCache:
    """Coordinate Redis product values with a manually bounded LRU/LFU index."""

    DIRTY_SET = "cache:dirty"

    def __init__(
        self,
        client: CacheClient,
        capacity: int = 64,
        policy: str | None = None,
        ttl_seconds: int = 300,
        local_ttl_seconds: float = 2.0,
    ) -> None:
        selected_policy = (policy or os.environ.get("CACHE_POLICY", "lru")).lower()
        if selected_policy not in {"lru", "lfu"}:
            raise ValueError("CACHE_POLICY must be either 'lru' or 'lfu'.")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        if local_ttl_seconds <= 0:
            raise ValueError("local_ttl_seconds must be positive.")

        self.client = client
        self.policy_name = selected_policy
        self.policy = (
            LRUPolicy(capacity) if selected_policy == "lru" else LFUPolicy(capacity)
        )
        self.ttl_seconds = ttl_seconds
        self.local_ttl_seconds = min(local_ttl_seconds, float(ttl_seconds))
        self._lock = threading.RLock()
        self._local_expirations: dict[str, float] = {}
        self._dirty_listener: Callable[[], None] | None = None
        self._hits = 0
        self._misses = 0
        self._local_hits = 0
        self._redis_hits = 0
        self._invalidation_errors = 0

    @staticmethod
    def _cache_key(product_id: int) -> str:
        return f"product:{product_id}"

    @staticmethod
    def _dirty_payload_key(key: str) -> str:
        return f"cache:dirty:{key}"

    @staticmethod
    def _serialize_product(product: Product) -> str:
        return json.dumps(
            {
                "product_id": product.product_id,
                "name": product.name,
                "sku": product.sku,
                "category": product.category,
                "quantity": product.quantity,
                "reorder_level": product.reorder_level,
                "unit_price": str(product.unit_price),
                "supplier_id": product.supplier_id,
                "updated_at": product.updated_at.isoformat(),
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize_product(value: str) -> Product:
        from datetime import datetime

        data: dict[str, Any] = json.loads(value)
        return Product(
            product_id=int(data["product_id"]),
            name=str(data["name"]),
            sku=str(data["sku"]),
            category=str(data["category"]),
            quantity=int(data["quantity"]),
            reorder_level=int(data["reorder_level"]),
            unit_price=Decimal(str(data["unit_price"])),
            supplier_id=int(data["supplier_id"]),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
        )

    def get_product(self, product_id: int) -> Product | None:
        key = self._cache_key(product_id)
        with self._lock:
            now = monotonic()
            local_value = self.policy.get(key)
            if local_value is not None:
                if self._local_expirations.get(key, 0) > now:
                    self._hits += 1
                    self._local_hits += 1
                    return self._deserialize_product(local_value)
                self.policy.remove(key)
                self._local_expirations.pop(key, None)

            value = self.client.get(key)
            if value is None:
                self.policy.remove(key)
                self._local_expirations.pop(key, None)
                self._misses += 1
                return None

            if key not in self.policy:
                self._evict_if_needed(self.policy.put(key, value))
            self._local_expirations[key] = now + self.local_ttl_seconds
            self._hits += 1
            self._redis_hits += 1
            return self._deserialize_product(value)

    def put_product(self, product: Product, dirty: bool = False) -> None:
        if product.product_id <= 0:
            raise ValueError("A cached product must have a positive product_id.")

        key = self._cache_key(product.product_id)
        value = self._serialize_product(product)
        with self._lock:
            if dirty:
                payload = json.dumps(
                    {"name": product.name, "unit_price": str(product.unit_price)},
                    separators=(",", ":"),
                )
                self.client.set_product_and_dirty(
                    key,
                    value,
                    self.ttl_seconds,
                    self._dirty_payload_key(key),
                    self.DIRTY_SET,
                    key,
                    payload,
                )
            else:
                self.client.set(key, value, ttl_seconds=self.ttl_seconds)
            self._evict_if_needed(self.policy.put(key, value))
            self._local_expirations[key] = monotonic() + self.local_ttl_seconds
            if dirty:
                listener = self._dirty_listener
            else:
                listener = None
        if listener is not None:
            listener()

    def _evict_if_needed(self, evicted_key: str | None) -> None:
        if evicted_key is not None:
            self._local_expirations.pop(evicted_key, None)
            self.client.delete(evicted_key)

    def invalidate_product(self, product_id: int) -> None:
        key = self._cache_key(product_id)
        with self._lock:
            self.policy.remove(key)
            self._local_expirations.pop(key, None)
            self.client.delete(key)

    def remove_product(self, product_id: int) -> None:
        key = self._cache_key(product_id)
        with self._lock:
            self.policy.remove(key)
            self._local_expirations.pop(key, None)
            self.client.remove_product_and_dirty(
                key,
                self._dirty_payload_key(key),
                self.DIRTY_SET,
                key,
            )

    def get_dirty_keys(self) -> set[str]:
        return self.client.smembers(self.DIRTY_SET)

    def get_dirty_record(self, key: str) -> tuple[str, dict[str, str]] | None:
        value = self.client.get(self._dirty_payload_key(key))
        if value is None:
            return None
        data: dict[str, Any] = json.loads(value)
        return value, {
            "name": str(data["name"]),
            "unit_price": str(Decimal(str(data["unit_price"]))),
        }

    def apply_pending_metadata(self, product: Product) -> Product:
        key = self._cache_key(product.product_id)
        record = self.get_dirty_record(key)
        if record is not None:
            _, metadata = record
            product.name = metadata["name"]
            product.unit_price = Decimal(metadata["unit_price"])
        return product

    def mark_clean(
        self,
        *keys: str,
        expected_values: dict[str, str] | None = None,
    ) -> None:
        with self._lock:
            for key in keys:
                if expected_values is None or key not in expected_values:
                    self.client.delete(self._dirty_payload_key(key))
                    self.client.srem(self.DIRTY_SET, key)
                else:
                    self.client.compare_and_clear_dirty(
                        self._dirty_payload_key(key),
                        self.DIRTY_SET,
                        key,
                        expected_values[key],
                    )

    def set_dirty_listener(self, listener: Callable[[], None] | None) -> None:
        with self._lock:
            self._dirty_listener = listener

    def record_invalidation_failure(self) -> None:
        with self._lock:
            self._invalidation_errors += 1

    def stats(self) -> dict[str, int | str | float]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "policy": self.policy_name,
                "size": len(self.policy),
                "capacity": self.policy.capacity,
                "dirty": len(self.get_dirty_keys()),
                "hits": self._hits,
                "misses": self._misses,
                "local_hits": self._local_hits,
                "redis_hits": self._redis_hits,
                "hit_rate": self._hits / total if total else 0.0,
                "invalidation_errors": self._invalidation_errors,
            }
