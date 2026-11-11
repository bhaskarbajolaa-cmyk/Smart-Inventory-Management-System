import argparse
import atexit
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from inventory.auth.service import Authentication
from inventory.concurrency.locks import LockManager
from inventory.concurrency.queue import PriorityJobQueue
from inventory.concurrency.retry import RetryPolicy
from inventory.concurrency.scheduler import RequestScheduler
from inventory.cache.client import CacheClient
from inventory.cache.flush_worker import FlushWorker
from inventory.cache.inventory_cache import InventoryCache
from inventory.cache.repository import CachedProductRepository
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.roles import RoleRepository
from inventory.persistence.repositories.suppliers import SupplierRepository
from inventory.persistence.repositories.transactions import StockTransactionRepository
from inventory.persistence.repositories.users import UserRepository
from inventory.app.services import ApplicationServices
from inventory.transactions.models import TransactionContext


def _run_seed(database_manager: "DatabaseManager") -> None:
    """Run the seeder using the already-connected DatabaseManager.

    Adds the scripts/ directory to sys.path so seed_data can be imported
    whether the app is launched from the project root or from elsewhere.
    """
    scripts_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # The seeder prints non-ASCII marks that crash Windows' default cp1252 console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        import seed_data  # type: ignore[import]

        # Reuse the already-connected singleton instead of opening a new connection
        from inventory.persistence.database import DatabaseManager as _DM
        _DM._instance = database_manager  # ensure seed uses our instance

        perm_ids = seed_data.seed_permissions(database_manager)
        seed_data.seed_admin_role(database_manager, perm_ids)
        seed_data.seed_manager_role(database_manager, perm_ids)
        seed_data.seed_supervisor_role(database_manager, perm_ids)
        seed_data.seed_default_admin_user(database_manager)
        supplier_ids = seed_data.seed_suppliers(database_manager)
        seed_data.seed_products(database_manager, supplier_ids)
    except Exception as exc:  # noqa: BLE001
        # Seeding is best-effort — never block app startup
        print(f"[seed] Warning: seeder raised {exc!r} — continuing without seed data.")


def build_application() -> ApplicationServices:
    """Create the shared dependency graph once for this application process."""
    database_manager = DatabaseManager.get_instance()
    database_manager.connect()

    # Seed roles, permissions, and default admin user on every startup.
    # Seeding is idempotent and safe to call repeatedly.
    _run_seed(database_manager)

    product_repository = ProductRepository(database_manager)
    lock_manager = LockManager()
    cache_client = CacheClient()
    cache_capacity = int(os.environ.get("CACHE_CAPACITY", "64"))
    inventory_cache = InventoryCache(cache_client, capacity=cache_capacity)
    cached_product_repository = CachedProductRepository(
        product_repository,
        inventory_cache,
        lock_manager,
    )
    flush_interval = float(os.environ.get("CACHE_FLUSH_INTERVAL", "30"))
    max_dirty = int(os.environ.get("CACHE_MAX_DIRTY", "100"))
    flush_worker = FlushWorker(
        inventory_cache,
        product_repository,
        lock_manager,
        flush_interval_seconds=flush_interval,
        max_dirty=max_dirty,
    )

    supplier_repository = SupplierRepository(database_manager)
    transaction_repository = StockTransactionRepository(database_manager)
    role_repository = RoleRepository(database_manager)
    user_repository = UserRepository(database_manager)
    authentication = Authentication(user_repository)
    retry_policy = RetryPolicy()
    # Use a single constant so queue capacity and scheduler depth limit are
    # always in sync — two independent limits caused confusing double-rejection.
    _max_depth = 100
    job_queue = PriorityJobQueue(capacity=_max_depth)
    scheduler = RequestScheduler(job_queue, retry_policy, max_queue_depth=_max_depth)
    transaction_context = TransactionContext(
        product_repository=product_repository,
        transaction_repository=transaction_repository,
        database_manager=database_manager,
        product_cache=inventory_cache,
    )

    services = ApplicationServices(
        database_manager=database_manager,
        product_repository=cached_product_repository,
        base_product_repository=product_repository,
        inventory_cache=inventory_cache,
        flush_worker=flush_worker,
        supplier_repository=supplier_repository,
        transaction_repository=transaction_repository,
        role_repository=role_repository,
        user_repository=user_repository,
        authentication=authentication,
        lock_manager=lock_manager,
        retry_policy=retry_policy,
        job_queue=job_queue,
        scheduler=scheduler,
        transaction_context=transaction_context,
    )
    flush_worker.start()
    atexit.register(flush_worker.stop, flush=True)
    return services


def main(argv: Sequence[str] | None = None) -> None:
    """Build shared services and launch either the CLI or HTTP backend."""
    env_port = os.environ.get("PORT")
    env_host = os.environ.get("HOST")
    default_port = int(env_port) if env_port else 5000
    default_host = env_host if env_host else ("0.0.0.0" if env_port else "127.0.0.1")

    parser = argparse.ArgumentParser(description="Smart Inventory Management System")
    parser.add_argument("--mode", choices=("cli", "server"), default="cli")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args(argv)

    try:
        services = build_application()
    except NotImplementedError:
        print(
            "Startup is wired, but DatabaseManager still has TODO methods. "
            "Complete the foundation contracts before running the application."
        )
        return

    if args.mode == "cli":
        from inventory.app.cli import InventoryCLI

        InventoryCLI(services).run()
        return

    from inventory.app.server import create_app

    create_app(services).run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()