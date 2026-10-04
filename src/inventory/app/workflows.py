"""Application workflows shared by CLI and HTTP interfaces."""

from datetime import datetime, timezone
from uuid import uuid4

from inventory.access_control.models import Role
from inventory.app.services import ApplicationServices
from inventory.auth.models import User
from inventory.concurrency.jobs import Job, JobResult
from inventory.transactions.models import Sale


def submit_sale_job(
    services: ApplicationServices,
    user: User,
    role: Role,
    product_id: int,
    quantity: int,
    customer_ref: str,
) -> str:
    """Create a sale operation, wrap it in a resource-locked job, and submit it."""
    product = services.product_repository.find_by_id(product_id)
    if product is None:
        raise LookupError(f"Product {product_id} does not exist.")
    if quantity < 1:
        raise ValueError("Sale quantity must be greater than zero.")

    sale = Sale(
        transaction_id=0,
        product_id=product_id,
        user_id=user.user_id,
        quantity=quantity,
        timestamp=datetime.now(timezone.utc),
        status="pending",
        sale_price=product.unit_price,
        customer_ref=customer_ref,
        sold_by_user_id=user.user_id,
    )

    def execute_sale() -> JobResult:
        return JobResult(
            succeeded=sale.execute(services.transaction_context),
        )

    job_id = str(uuid4())
    job = Job(
        job_id=job_id,
        job_type="sale",
        resource_id=str(product_id),
        priority=role.default_job_priority,
        attempt=0,
        idempotency_key=job_id,
        operation=execute_sale,
        lock_manager=services.lock_manager,
    )
    return services.scheduler.submit(job)