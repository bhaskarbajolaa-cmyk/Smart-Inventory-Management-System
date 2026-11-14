from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from inventory.cache.client import CacheClient
from inventory.cache.eviction import LFUPolicy, LRUPolicy
from inventory.cache.flush_worker import FlushWorker
from inventory.cache.inventory_cache import InventoryCache
from inventory.cache.repository import CachedProductRepository
from inventory.app.server import create_app
from inventory.catalog.models import Product, Supplier
from inventory.concurrency.locks import LockManager
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.suppliers import SupplierRepository
from inventory.transactions.models import Sale, TransactionContext


class MockTransactionRepository:
    def save(self, transaction: Sale) -> Sale:
        return transaction


class InventoryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_name=":memory:")
        self.db.connect()
        self.repository = ProductRepository(self.db)
        supplier = SupplierRepository(self.db).save(
            Supplier(0, "Cache Supplier", "cache@example.com", "123", "Address")
        )
        self.product = self.repository.save(
            Product(
                0,
                "Original Product",
                "CACHE-001",
                "Demo",
                20,
                2,
                Decimal("10.00"),
                supplier.supplier_id,
                datetime.now(timezone.utc),
            )
        )
        self.client = CacheClient(url="")
        self.cache = InventoryCache(self.client, capacity=2)
        self.locks = LockManager()

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_lru_eviction_order(self) -> None:
        policy = LRUPolicy(capacity=2)
        policy.put("a", "1")
        policy.put("b", "2")
        self.assertEqual(policy.get("a"), "1")
        self.assertEqual(policy.put("c", "3"), "b")
        self.assertNotIn("b", policy)

    def test_lfu_eviction_frequency(self) -> None:
        policy = LFUPolicy(capacity=2)
        policy.put("a", "1")
        policy.put("b", "2")
        policy.get("a")
        self.assertEqual(policy.put("c", "3"), "b")

    def test_lfu_lru_tiebreak(self) -> None:
        policy = LFUPolicy(capacity=2)
        policy.put("a", "1")
        policy.put("b", "2")
        self.assertEqual(policy.put("c", "3"), "a")

    def test_cache_put_dirty_adds_to_dirty_set(self) -> None:
        self.cache.put_product(self.product, dirty=True)
        self.assertEqual(
            self.cache.get_dirty_keys(),
            {f"product:{self.product.product_id}"},
        )

    def test_cache_read_miss_then_hit(self) -> None:
        self.assertIsNone(self.cache.get_product(self.product.product_id))
        self.cache.put_product(self.product)
        self.assertEqual(
            self.cache.get_product(self.product.product_id).name,
            self.product.name,
        )
        self.assertEqual(self.cache.stats()["hits"], 1)
        self.assertEqual(self.cache.stats()["misses"], 1)

    def test_warm_local_hit_does_not_call_redis(self) -> None:
        self.cache.put_product(self.product)

        with patch.object(self.client, "get", wraps=self.client.get) as redis_get:
            cached = self.cache.get_product(self.product.product_id)

        self.assertEqual(cached.name, self.product.name)
        redis_get.assert_not_called()
        self.assertEqual(self.cache.stats()["local_hits"], 1)

    def test_expired_local_value_falls_back_to_redis(self) -> None:
        with patch("inventory.cache.inventory_cache.monotonic", return_value=0):
            self.cache.put_product(self.product)

        with (
            patch("inventory.cache.inventory_cache.monotonic", return_value=3),
            patch.object(self.client, "get", wraps=self.client.get) as redis_get,
        ):
            cached = self.cache.get_product(self.product.product_id)

        self.assertEqual(cached.name, self.product.name)
        redis_get.assert_called_once_with(f"product:{self.product.product_id}")
        self.assertEqual(self.cache.stats()["redis_hits"], 1)

    def test_invalidation_removes_local_value(self) -> None:
        self.cache.put_product(self.product)
        self.cache.invalidate_product(self.product.product_id)

        with patch.object(self.client, "get", wraps=self.client.get) as redis_get:
            self.assertIsNone(self.cache.get_product(self.product.product_id))

        redis_get.assert_called_once_with(f"product:{self.product.product_id}")

    def test_cached_repository_reports_miss_then_hit(self) -> None:
        cached_repository = CachedProductRepository(
            self.repository,
            self.cache,
            self.locks,
        )
        self.assertIsNotNone(cached_repository.find_by_id(self.product.product_id))
        self.assertEqual(cached_repository.last_cache_status, "MISS")
        self.assertIsNotNone(cached_repository.find_by_id(self.product.product_id))
        self.assertEqual(cached_repository.last_cache_status, "HIT")

    def test_api_product_route_reports_cache_status(self) -> None:
        cached_repository = CachedProductRepository(
            self.repository,
            self.cache,
            self.locks,
        )
        user = SimpleNamespace(
            user_id=1,
            role_id=1,
            has_permission=lambda _code, _role: True,
        )
        session = SimpleNamespace(
            user_id=1,
            is_expired=lambda: False,
        )
        services = SimpleNamespace(
            inventory_cache=self.cache,
            product_repository=cached_repository,
            authentication=SimpleNamespace(get_session=lambda _token: session),
            user_repository=SimpleNamespace(find_by_id=lambda _user_id: user),
            role_repository=SimpleNamespace(find_by_id=lambda _role_id: object()),
        )
        client = create_app(services).test_client()

        first = client.get(
            f"/api/products/{self.product.product_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        second = client.get(
            f"/api/products/{self.product.product_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        health = client.get("/health")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["X-Cache"], "MISS")
        self.assertEqual(second.headers["X-Cache"], "HIT")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json["cache"]["policy"], "lru")

    def test_api_cache_benchmark_compares_both_read_paths(self) -> None:
        cached_repository = CachedProductRepository(
            self.repository,
            self.cache,
            self.locks,
        )
        user = SimpleNamespace(
            user_id=1,
            role_id=1,
            has_permission=lambda _code, _role: True,
        )
        session = SimpleNamespace(
            user_id=1,
            is_expired=lambda: False,
        )
        services = SimpleNamespace(
            inventory_cache=self.cache,
            product_repository=cached_repository,
            base_product_repository=self.repository,
            database_manager=self.db,
            lock_manager=self.locks,
            authentication=SimpleNamespace(get_session=lambda _token: session),
            user_repository=SimpleNamespace(find_by_id=lambda _user_id: user),
            role_repository=SimpleNamespace(find_by_id=lambda _role_id: object()),
        )
        response = create_app(services).test_client().post(
            "/api/cache/benchmark",
            headers={"Authorization": "Bearer test-token"},
            json={
                "iterations": 10,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["database"]["reads"], 10)
        self.assertEqual(response.json["cache"]["reads"], 10)
        self.assertEqual(response.json["cache"]["policy"], "lru")
        self.assertEqual(response.json["cache"]["first_read_status"], "MISS")
        self.assertEqual(response.json["cache"]["local_hits"], 10)
        self.assertEqual(response.json["series"][0]["requests"], 0)
        self.assertEqual(response.json["series"][-1]["requests"], 10)
        self.assertAlmostEqual(
            response.json["series"][-1]["cache_total_ms"],
            response.json["cache"]["total_ms"],
        )
        self.assertEqual(self.repository.find_by_id(self.product.product_id).quantity, 20)
        self.assertNotIn("product_name", response.json)

    def test_api_cache_benchmark_rejects_excessive_iterations(self) -> None:
        cached_repository = CachedProductRepository(
            self.repository,
            self.cache,
            self.locks,
        )
        user = SimpleNamespace(
            user_id=1,
            role_id=1,
            has_permission=lambda _code, _role: True,
        )
        session = SimpleNamespace(user_id=1, is_expired=lambda: False)
        services = SimpleNamespace(
            inventory_cache=self.cache,
            product_repository=cached_repository,
            base_product_repository=self.repository,
            database_manager=self.db,
            lock_manager=self.locks,
            authentication=SimpleNamespace(get_session=lambda _token: session),
            user_repository=SimpleNamespace(find_by_id=lambda _user_id: user),
            role_repository=SimpleNamespace(find_by_id=lambda _role_id: object()),
        )
        response = create_app(services).test_client().post(
            "/api/cache/benchmark",
            headers={"Authorization": "Bearer test-token"},
            json={"iterations": 151},
        )
        self.assertEqual(response.status_code, 400)

    def _page_client(self):
        session = SimpleNamespace(
            user_id=1, is_expired=lambda: False, session_token="page-token"
        )
        user = SimpleNamespace(
            user_id=1, role_id=1, has_permission=lambda _code, _role: True
        )
        services = SimpleNamespace(
            authentication=SimpleNamespace(
                get_session=lambda token: session if token == "page-token" else None
            ),
            user_repository=SimpleNamespace(find_by_id=lambda _user_id: user),
            role_repository=SimpleNamespace(find_by_id=lambda _role_id: object()),
        )
        client = create_app(services).test_client()
        client.set_cookie("smobhar_session_token", "page-token")
        return client

    def test_cache_demo_page_is_registered(self) -> None:
        client = self._page_client()
        response = client.get("/cache-demo")
        traffic_page = client.get("/cache-traffic")
        home = client.get("/home")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Does the cache make product reads faster?", response.data)
        self.assertIn(b"cache-traffic", response.data)
        self.assertNotIn(b"trafficChart", response.data)
        self.assertIn(b"cache_demo.js?v=7", response.data)
        self.assertEqual(traffic_page.status_code, 200)
        self.assertIn(b"Requests reaching the application", traffic_page.data)
        self.assertIn(b"cache_traffic.js?v=1", traffic_page.data)
        self.assertNotIn(b"Cache Demo", home.data)

    def test_live_request_metrics_exclude_polling_and_health_checks(self) -> None:
        client = create_app(SimpleNamespace()).test_client()
        client.get("/cache-demo")
        client.get("/health")
        first = client.get("/api/cache/traffic")
        second = client.get("/api/cache/traffic")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["total_requests"], 1)
        self.assertEqual(first.json["recent_requests"], 1)
        self.assertEqual(len(first.json["buckets"]), 12)
        self.assertEqual(sum(first.json["buckets"]), 1)
        self.assertEqual(second.json["total_requests"], 1)

    def test_flush_worker_drains_dirty_set(self) -> None:
        self.product.name = "Buffered Product"
        self.product.unit_price = Decimal("15.25")
        self.cache.put_product(self.product, dirty=True)
        worker = FlushWorker(self.cache, self.repository, self.locks)

        self.assertEqual(worker.flush_now(), 1)
        self.assertEqual(self.cache.get_dirty_keys(), set())
        saved = self.repository.find_by_id(self.product.product_id)
        self.assertEqual(saved.name, "Buffered Product")
        self.assertEqual(saved.unit_price, Decimal("15.25"))

    def test_flush_worker_shutdown_flushes(self) -> None:
        self.product.name = "Shutdown Flush"
        self.cache.put_product(self.product, dirty=True)
        worker = FlushWorker(
            self.cache,
            self.repository,
            self.locks,
            flush_interval_seconds=60,
        )
        worker.start()
        worker.stop(flush=True)

        self.assertEqual(self.repository.find_by_id(self.product.product_id).name, "Shutdown Flush")
        self.assertEqual(self.cache.get_dirty_keys(), set())

    def test_cache_invalidate_preserves_pending_metadata(self) -> None:
        self.product.name = "Still Pending"
        self.cache.put_product(self.product, dirty=True)
        self.cache.invalidate_product(self.product.product_id)

        self.assertIsNone(self.cache.get_product(self.product.product_id))
        self.assertEqual(self.cache.get_dirty_keys(), {f"product:{self.product.product_id}"})
        fresh = self.repository.find_by_id(self.product.product_id)
        self.cache.apply_pending_metadata(fresh)
        self.assertEqual(fresh.name, "Still Pending")

    def test_eviction_triggers_redis_delete(self) -> None:
        self.cache.put_product(self.product)
        second = Product(
            self.product.product_id + 1,
            "Second Product",
            "CACHE-002",
            "Demo",
            1,
            0,
            Decimal("2.00"),
            self.product.supplier_id,
            datetime.now(timezone.utc),
        )
        self.cache.put_product(second)
        third = Product(
            self.product.product_id + 2,
            "Third Product",
            "CACHE-003",
            "Demo",
            1,
            0,
            Decimal("3.00"),
            self.product.supplier_id,
            datetime.now(timezone.utc),
        )
        self.cache.put_product(third)

        self.assertIsNone(self.client.get(f"product:{self.product.product_id}"))

    def test_metadata_write_behind_keeps_stock_synchronous(self) -> None:
        cached_repository = CachedProductRepository(
            self.repository,
            self.cache,
            self.locks,
        )
        edited = Product(
            self.product.product_id,
            "Deferred Name",
            self.product.sku,
            self.product.category,
            14,
            self.product.reorder_level,
            Decimal("19.99"),
            self.product.supplier_id,
            datetime.now(timezone.utc),
        )
        cached_repository.save(edited)

        persisted = self.repository.find_by_id(self.product.product_id)
        self.assertEqual(persisted.quantity, 14)
        self.assertEqual(persisted.name, "Original Product")
        self.assertEqual(persisted.unit_price, Decimal("10.00"))

        cached = cached_repository.find_by_id(self.product.product_id)
        self.assertEqual(cached.name, "Deferred Name")
        self.assertEqual(cached.quantity, 14)

        FlushWorker(self.cache, self.repository, self.locks).flush_now()
        persisted = self.repository.find_by_id(self.product.product_id)
        self.assertEqual(persisted.quantity, 14)
        self.assertEqual(persisted.name, "Deferred Name")
        self.assertEqual(persisted.unit_price, Decimal("19.99"))

    def test_committed_sale_invalidates_cached_product(self) -> None:
        self.cache.put_product(self.product)
        context = TransactionContext(
            product_repository=self.repository,
            transaction_repository=MockTransactionRepository(),
            database_manager=self.db,
            product_cache=self.cache,
        )
        sale = Sale(
            transaction_id=0,
            product_id=self.product.product_id,
            user_id=1,
            quantity=2,
            timestamp=datetime.now(timezone.utc),
            sale_price=self.product.unit_price,
        )

        self.assertTrue(sale.execute(context))
        self.assertIsNone(self.cache.get_product(self.product.product_id))
        self.assertEqual(
            self.repository.find_by_id(self.product.product_id).quantity,
            18,
        )

    def test_database_queries_wait_for_open_transaction(self) -> None:
        self.db.begin_transaction()
        self.db.execute_query(
            "UPDATE products SET quantity = ? WHERE product_id = ?",
            (18, self.product.product_id),
        )
        started = threading.Event()
        finished = threading.Event()

        def update_product() -> None:
            started.set()
            self.db.execute_query(
                "UPDATE products SET quantity = ? WHERE product_id = ?",
                (17, self.product.product_id),
            )
            finished.set()

        thread = threading.Thread(target=update_product)
        thread.start()
        self.assertTrue(started.wait(timeout=1))
        time.sleep(0.05)
        self.assertFalse(finished.is_set())
        self.db.commit()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            self.repository.find_by_id(self.product.product_id).quantity,
            17,
        )

    def test_redundant_rollback_cannot_rollback_another_thread(self) -> None:
        self.db.begin_transaction()
        self.db.rollback()
        transaction_started = threading.Event()

        def update_product() -> None:
            self.db.begin_transaction()
            transaction_started.set()
            self.db.execute_query(
                "UPDATE products SET quantity = ? WHERE product_id = ?",
                (16, self.product.product_id),
            )
            self.db.commit()

        thread = threading.Thread(target=update_product)
        thread.start()
        self.assertTrue(transaction_started.wait(timeout=1))
        self.db.rollback()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            self.repository.find_by_id(self.product.product_id).quantity,
            16,
        )


if __name__ == "__main__":
    unittest.main()
