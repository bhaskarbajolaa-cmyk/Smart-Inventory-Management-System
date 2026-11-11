"""Run concurrent sale/purchase operations against an isolated temporary database."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from inventory.cache.client import CacheClient
from inventory.cache.flush_worker import FlushWorker
from inventory.cache.inventory_cache import InventoryCache
from inventory.cache.repository import CachedProductRepository
from inventory.catalog.models import Product
from inventory.concurrency.locks import LockManager
from inventory.persistence.config import DatabaseConfig
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.transactions import StockTransactionRepository
from inventory.transactions.models import Purchase, Sale, TransactionContext


def _create_fixture(
    db: DatabaseManager,
    starting_stock: int,
) -> tuple[int, int, int]:
    supplier_rows = db.execute_query(
        "INSERT INTO suppliers (name, contact_email, phone, address) "
        "VALUES (?, ?, ?, ?) RETURNING supplier_id",
        ("Stress Test Supplier", "stress@example.invalid", "000", "Temporary"),
    )
    supplier_id = int(supplier_rows[0]["supplier_id"])
    user_rows = db.execute_query(
        "INSERT INTO users (username, email, role_id, password_hash, created_at) "
        "VALUES (?, ?, NULL, NULL, ?) RETURNING user_id",
        ("stress-test-user", "stress-user@example.invalid", datetime.now(timezone.utc).isoformat()),
    )
    user_id = int(user_rows[0]["user_id"])
    product = Product(
        product_id=0,
        name="Temporary Stress Product",
        sku="STRESS-TEMP-001",
        category="Stress Test",
        quantity=starting_stock,
        reorder_level=0,
        unit_price=Decimal("1.00"),
        supplier_id=supplier_id,
        updated_at=datetime.now(timezone.utc),
    )
    saved_product = ProductRepository(db).save(product)
    return saved_product.product_id, user_id, supplier_id


def run_stress_test(transactions: int, workers: int, mode: str) -> int:
    if transactions <= 0 or workers <= 0:
        raise ValueError("transactions and workers must be positive.")
    if mode not in {"sale", "mixed"}:
        raise ValueError("mode must be either 'sale' or 'mixed'.")

    initial_stock = max(1, transactions // 2) if mode == "sale" else max(1, transactions // 4)
    with tempfile.TemporaryDirectory(prefix="inventory-stress-") as directory:
        database_path = str(Path(directory) / "stress.db")
        db = DatabaseManager(config=DatabaseConfig(db_name=database_path))
        db.connect()
        try:
            product_id, user_id, supplier_id = _create_fixture(db, initial_stock)
            product_repo = ProductRepository(db)
            transaction_repo = StockTransactionRepository(db)
            cache = InventoryCache(CacheClient(url=""), capacity=64)
            lock_manager = LockManager(default_timeout_ms=60_000, lease_ms=60_000)
            cached_repo = CachedProductRepository(product_repo, cache, lock_manager)
            context = TransactionContext(
                product_repository=product_repo,
                transaction_repository=transaction_repo,
                database_manager=db,
                product_cache=cache,
            )
            worker = FlushWorker(cache, product_repo, lock_manager)

            initial_product = product_repo.find_by_id(product_id)
            if initial_product is None:
                raise RuntimeError("Stress-test fixture product was not created.")
            initial_product.name = "Buffered Stress Product"
            cache.put_product(initial_product, dirty=True)
            cached_repo.find_by_id(product_id)

            successes = 0
            failures = 0
            sale_successes = 0
            purchase_successes = 0
            latencies: list[float] = []
            successful_transaction_ids: set[int] = set()

            def execute_one(index: int) -> tuple[bool, str, float, int | None]:
                import time
                import uuid

                started = time.perf_counter()
                token = lock_manager.acquire(
                    str(product_id),
                    owner_id=f"stress:{index}:{uuid.uuid4()}",
                    timeout_ms=60_000,
                )
                if token is None:
                    return False, "lock", time.perf_counter() - started, None

                try:
                    cached_repo.find_by_id(product_id)
                    is_purchase = mode == "mixed" and index % 2 == 1
                    if is_purchase:
                        transaction = Purchase(
                            transaction_id=0,
                            product_id=product_id,
                            user_id=user_id,
                            quantity=1,
                            timestamp=datetime.now(timezone.utc),
                            unit_cost=Decimal("1.00"),
                            supplier_id=supplier_id,
                            ordered_by_user_id=user_id,
                        )
                    else:
                        transaction = Sale(
                            transaction_id=0,
                            product_id=product_id,
                            user_id=user_id,
                            quantity=1,
                            timestamp=datetime.now(timezone.utc),
                            sale_price=Decimal("1.00"),
                            customer_ref=f"stress-{index}",
                            sold_by_user_id=user_id,
                        )
                    try:
                        transaction.execute(context)
                        return (
                            True,
                            "purchase" if is_purchase else "sale",
                            time.perf_counter() - started,
                            transaction.transaction_id,
                        )
                    except ValueError:
                        return False, "stock", time.perf_counter() - started, None
                finally:
                    lock_manager.release(token)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(execute_one, index) for index in range(transactions)]
                for future in as_completed(futures):
                    succeeded, kind, latency, transaction_id = future.result()
                    latencies.append(latency)
                    if succeeded:
                        successes += 1
                        if transaction_id is not None:
                            successful_transaction_ids.add(transaction_id)
                        if kind == "sale":
                            sale_successes += 1
                        elif kind == "purchase":
                            purchase_successes += 1
                    else:
                        failures += 1

            flushed = worker.flush_now()
            final_product = product_repo.find_by_id(product_id)
            if final_product is None:
                raise RuntimeError("Stress-test fixture product disappeared.")

            totals = db.execute_query(
                "SELECT transaction_type, COALESCE(SUM(quantity), 0) AS quantity, "
                "COUNT(*) AS count FROM stock_transactions WHERE product_id = ? "
                "GROUP BY transaction_type",
                (product_id,),
            )
            quantities = {str(row["transaction_type"]): int(row["quantity"]) for row in totals}
            record_count = sum(int(row["count"]) for row in totals)
            recorded_transaction_ids = {
                transaction.transaction_id
                for transaction in transaction_repo.find_by_product(product_id)
            }
            expected_stock = (
                initial_stock
                + quantities.get("purchase", 0)
                - quantities.get("sale", 0)
            )
            consistent = (
                final_product.quantity == expected_stock
                and record_count == successes
                and len(successful_transaction_ids) == successes
                and successful_transaction_ids.issubset(recorded_transaction_ids)
                and not cache.get_dirty_keys()
            )
            stats = cache.stats()
            average_latency = sum(latencies) / len(latencies) if latencies else 0.0
            print("=== Stress Test Results ===")
            print(f"Transactions attempted:  {transactions}")
            print(
                f"  Succeeded:             {successes} "
                f"(sales {sale_successes}, purchases {purchase_successes})"
            )
            print(f"  Failed (stock/lock):   {failures}")
            print(f"Avg latency:             {average_latency * 1000:.2f} ms")
            print(f"Cache hit rate:          {stats['hit_rate']:.0%}")
            print(f"Dirty items flushed:     {flushed}")
            print(f"DB consistency check:    {'PASSED' if consistent else 'FAILED'}")
            return 0 if consistent else 1
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transactions", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--mode", choices=("sale", "mixed"), default="sale")
    args = parser.parse_args()
    return run_stress_test(args.transactions, args.workers, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
