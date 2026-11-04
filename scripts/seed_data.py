"""Seed demo roles, users, products, and suppliers."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib

from inventory.catalog.models import Product, Supplier
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.suppliers import SupplierRepository


def main() -> None:
    """Create the deterministic demo dataset."""
    db = DatabaseManager.get_instance()
    db.connect()

    # 1. System Permissions
    permissions = [
        (1, "VIEW", "View records"),
        (2, "EDIT", "Edit records"),
        (3, "APPEND", "Append/insert records"),
    ]
    for perm_id, code, desc in permissions:
        db.execute_query(
            "INSERT OR IGNORE INTO permissions (permission_id, code, description) "
            "VALUES (?, ?, ?)",
            (perm_id, code, desc),
        )

    # 2. Roles
    db.execute_query(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_by, is_system_default, default_job_priority) "
        "VALUES (1, 'Admin', 1, 1, 3)"
    )

    # 3. Table Access Grants (Admin gets global VIEW, EDIT, APPEND)
    now_str = datetime.now(timezone.utc).isoformat()
    for grant_id, perm_id in [(1, 1), (2, 2), (3, 3)]:
        db.execute_query(
            "INSERT OR IGNORE INTO table_access_grants "
            "(grant_id, role_id, permission_id, table_name, granted_by, granted_at) "
            "VALUES (?, 1, ?, NULL, 1, ?)",
            (grant_id, perm_id, now_str),
        )

    # 4. Admin User (admin / admin123)
    pwd_hash = hashlib.sha256("admin123".encode("utf-8")).hexdigest()
    db.execute_query(
        "INSERT OR IGNORE INTO users (user_id, username, email, role_id, password_hash, created_at) "
        "VALUES (1, 'admin', 'admin@example.com', 1, ?, ?)",
        (pwd_hash, now_str),
    )

    # 5. Suppliers
    supplier_repo = SupplierRepository(db)
    if not supplier_repo.find_all():
        supplier = supplier_repo.save(
            Supplier(
                supplier_id=0,
                name="Acme Tech Supplies",
                contact_email="contact@acme.com",
                phone="+1-555-0199",
                address="100 Tech Park, Silicon Valley",
            )
        )
    else:
        supplier = supplier_repo.find_all()[0]

    # 6. Products
    product_repo = ProductRepository(db)
    if not product_repo.find_all():
        now = datetime.now()
        product_repo.save(
            Product(
                product_id=0,
                name="Wireless Mouse",
                sku="WM-100",
                category="Electronics",
                quantity=50,
                reorder_level=10,
                unit_price=Decimal("29.99"),
                supplier_id=supplier.supplier_id,
                updated_at=now,
            )
        )
        product_repo.save(
            Product(
                product_id=0,
                name="Mechanical Keyboard",
                sku="KB-200",
                category="Electronics",
                quantity=30,
                reorder_level=5,
                unit_price=Decimal("79.99"),
                supplier_id=supplier.supplier_id,
                updated_at=now,
            )
        )
        product_repo.save(
            Product(
                product_id=0,
                name="USB-C Cable",
                sku="CB-300",
                category="Accessories",
                quantity=8,
                reorder_level=15,
                unit_price=Decimal("12.50"),
                supplier_id=supplier.supplier_id,
                updated_at=now,
            )
        )

    print("Deterministic demo dataset seeded successfully into inventory.db!")
    print("Demo Admin User: admin")
    print("Demo Password:   admin123")


if __name__ == "__main__":
    main()
