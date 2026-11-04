"""Demonstrate concurrent sales against a shared product."""

from inventory.app.main import build_application
from inventory.app.workflows import submit_sale_job


def main() -> None:
    """Run concurrent sales and report the final stock quantity."""
    services = build_application()
    session = services.authentication.login("admin", "admin123")
    user = services.user_repository.find_by_id(session.user_id)
    if user is None:
        raise LookupError("Admin user not found. Run scripts/seed_data.py first.")
    role = services.role_repository.find_by_id(user.role_id)
    if role is None:
        raise LookupError("Role not found.")

    product = services.product_repository.find_by_id(1)
    if product is None:
        raise LookupError("Product 1 not found. Run scripts/seed_data.py first.")

    start_stock = product.quantity
    sales_count = 5
    units_per_sale = 2
    print(f"Starting stock for Product {product.product_id} ({product.name}): {start_stock}")
    print(f"Submitting {sales_count} simultaneous sale jobs ({units_per_sale} units each)...")

    job_ids = []
    for i in range(sales_count):
        job_id = submit_sale_job(
            services=services,
            user=user,
            role=role,
            product_id=product.product_id,
            quantity=units_per_sale,
            customer_ref=f"concurrency-demo-{i + 1}",
        )
        job_ids.append(job_id)
        print(f"  Enqueued job {i + 1}: {job_id}")

    print("Waiting for scheduler to process all jobs under lock control...")
    idle = services.scheduler.wait_for_idle(timeout_seconds=15)
    services.scheduler.shutdown_gracefully()

    if not idle:
        print("Warning: Timed out waiting for workers.")

    updated_product = services.product_repository.find_by_id(product.product_id)
    final_stock = updated_product.quantity if updated_product else 0
    expected_stock = start_stock - (sales_count * units_per_sale)

    print("\n--- Concurrency Demo Results ---")
    print(f"Total jobs executed: {len(job_ids)}")
    print(f"Initial stock:       {start_stock}")
    print(f"Expected final stock:{expected_stock}")
    print(f"Actual final stock:  {final_stock}")
    assert final_stock == expected_stock, "Race condition detected! Stock mismatch."
    print("SUCCESS: Mutual exclusion and stock updates verified!")


if __name__ == "__main__":
    main()
