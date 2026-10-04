"""Application composition root with CLI and backend-server launch modes."""

import argparse
from collections.abc import Sequence

from inventory.auth.service import Authentication
from inventory.concurrency.locks import LockManager
from inventory.concurrency.queue import PriorityJobQueue
from inventory.concurrency.retry import RetryPolicy
from inventory.concurrency.scheduler import RequestScheduler
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.roles import RoleRepository
from inventory.persistence.repositories.suppliers import SupplierRepository
from inventory.persistence.repositories.transactions import StockTransactionRepository
from inventory.persistence.repositories.users import UserRepository
from inventory.app.services import ApplicationServices
from inventory.transactions.models import TransactionContext


def build_application() -> ApplicationServices:
    """Create the shared dependency graph once for this application process."""
    database_manager = DatabaseManager.get_instance()
    database_manager.connect()

    product_repository = ProductRepository(database_manager)
    supplier_repository = SupplierRepository(database_manager)
    transaction_repository = StockTransactionRepository(database_manager)
    role_repository = RoleRepository(database_manager)
    user_repository = UserRepository(database_manager)
    authentication = Authentication(user_repository)
    lock_manager = LockManager()
    retry_policy = RetryPolicy()
    job_queue = PriorityJobQueue()
    scheduler = RequestScheduler(job_queue, retry_policy)
    transaction_context = TransactionContext(
        product_repository=product_repository,
        transaction_repository=transaction_repository,
        database_manager=database_manager,
    )

    return ApplicationServices(
        database_manager=database_manager,
        product_repository=product_repository,
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


def main(argv: Sequence[str] | None = None) -> None:
    """Build shared services and launch either the CLI or HTTP backend."""
    parser = argparse.ArgumentParser(description="Smart Inventory Management System")
    parser.add_argument("--mode", choices=("cli", "server"), default="cli")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
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