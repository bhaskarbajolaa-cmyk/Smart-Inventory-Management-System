"""Shared dependency container for CLI and HTTP application interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.auth.service import Authentication
    from inventory.concurrency.locks import LockManager
    from inventory.concurrency.queue import PriorityJobQueue
    from inventory.concurrency.retry import RetryPolicy
    from inventory.concurrency.scheduler import RequestScheduler
    from inventory.cache.flush_worker import FlushWorker
    from inventory.cache.inventory_cache import InventoryCache
    from inventory.persistence.database import DatabaseManager
    from inventory.cache.repository import CachedProductRepository
    from inventory.persistence.repositories.products import ProductRepository
    from inventory.persistence.repositories.roles import RoleRepository
    from inventory.persistence.repositories.suppliers import SupplierRepository
    from inventory.persistence.repositories.transactions import StockTransactionRepository
    from inventory.persistence.repositories.users import UserRepository
    from inventory.transactions.models import TransactionContext
    from inventory.analytics.service import AnalyticsService


@dataclass
class ApplicationServices:
    """Shared dependencies passed to CLI handlers and HTTP route handlers."""

    database_manager: DatabaseManager
    product_repository: CachedProductRepository
    base_product_repository: ProductRepository
    inventory_cache: InventoryCache
    flush_worker: FlushWorker
    supplier_repository: SupplierRepository
    transaction_repository: StockTransactionRepository
    role_repository: RoleRepository
    user_repository: UserRepository
    authentication: Authentication
    lock_manager: LockManager
    retry_policy: RetryPolicy
    job_queue: PriorityJobQueue
    scheduler: RequestScheduler
    transaction_context: TransactionContext
    analytics_service: AnalyticsService | None = None