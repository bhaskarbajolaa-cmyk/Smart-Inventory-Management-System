"""Seed the database with initial roles, permissions, users, suppliers, and products.

Run from the project root (where pyproject.toml lives):

    python scripts/seed_data.py

Or via the installed entry-point after `pip install -e .[dev]`:

    python -m inventory.app.main --mode cli   # starts app (builds DB first)
    python scripts/seed_data.py               # then seeds

The seeder is safe to re-run — it uses INSERT OR IGNORE / UPDATE OR IGNORE
patterns so existing data is not duplicated.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

# Allow running as a plain script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from inventory.persistence.database import DatabaseManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def seed_permissions(db: DatabaseManager) -> dict[str, int]:
    """Insert core permission codes. Returns {code: permission_id}."""
    permissions = [
        ("VIEW",   "Read / list records"),
        ("APPEND", "Insert new records"),
        ("EDIT",   "Update existing records"),
        ("DELETE", "Remove records"),
        ("ADMIN",  "Full administrative control"),
    ]

    result: dict[str, int] = {}
    for code, description in permissions:
        db.execute_query(
            "INSERT OR IGNORE INTO permissions (code, description) VALUES (?, ?)",
            (code, description),
        )
        rows = db.execute_query(
            "SELECT permission_id FROM permissions WHERE code = ?", (code,)
        )
        result[code] = int(rows[0]["permission_id"])

    print(f"  ✓ {len(result)} permissions seeded: {', '.join(result)}")
    return result


def seed_admin_role(db: DatabaseManager, perm_ids: dict[str, int]) -> int:
    """Insert the Admin system role and its grants. Returns role_id."""
    db.execute_query(
        """INSERT OR IGNORE INTO roles
           (role_id, role_name, created_by, is_system_default, default_job_priority)
           VALUES (1, 'Admin', 0, 1, 1)""",
    )
    rows = db.execute_query("SELECT role_id FROM roles WHERE role_id = 1")
    role_id = int(rows[0]["role_id"])

    # Grant every permission globally (table_name = NULL)
    for code, pid in perm_ids.items():
        db.execute_query(
            """INSERT OR IGNORE INTO table_access_grants
               (role_id, permission_id, table_name, granted_by, granted_at)
               VALUES (?, ?, NULL, 0, ?)""",
            (role_id, pid, _now()),
        )

    print(f"  ✓ Admin role (id=1) seeded with {len(perm_ids)} global grants")
    return role_id


def seed_manager_role(db: DatabaseManager, perm_ids: dict[str, int]) -> int:
    """Insert the Manager role with VIEW + APPEND + EDIT. Returns role_id."""
    db.execute_query(
        """INSERT OR IGNORE INTO roles
           (role_id, role_name, created_by, is_system_default, default_job_priority)
           VALUES (2, 'Manager', 0, 1, 2)""",
    )
    rows = db.execute_query("SELECT role_id FROM roles WHERE role_id = 2")
    role_id = int(rows[0]["role_id"])

    manager_perms = ["VIEW", "APPEND", "EDIT"]
    for code in manager_perms:
        pid = perm_ids[code]
        db.execute_query(
            """INSERT OR IGNORE INTO table_access_grants
               (role_id, permission_id, table_name, granted_by, granted_at)
               VALUES (?, ?, NULL, 0, ?)""",
            (role_id, pid, _now()),
        )

    print(f"  ✓ Manager role (id=2) seeded with {manager_perms}")
    return role_id


def seed_supervisor_role(db: DatabaseManager, perm_ids: dict[str, int]) -> int:
    """Insert the Supervisor role with VIEW only. Returns role_id."""
    db.execute_query(
        """INSERT OR IGNORE INTO roles
           (role_id, role_name, created_by, is_system_default, default_job_priority)
           VALUES (3, 'Supervisor', 0, 1, 3)""",
    )
    rows = db.execute_query("SELECT role_id FROM roles WHERE role_id = 3")
    role_id = int(rows[0]["role_id"])

    db.execute_query(
        """INSERT OR IGNORE INTO table_access_grants
           (role_id, permission_id, table_name, granted_by, granted_at)
           VALUES (?, ?, NULL, 0, ?)""",
        (role_id, perm_ids["VIEW"], _now()),
    )

    print("  ✓ Supervisor role (id=3) seeded with VIEW only")
    return role_id


def seed_default_admin_user(db: DatabaseManager) -> int:
    """Insert the default admin user. Returns user_id."""
    username = "admin"
    email = "admin@smobhar.local"
    password_hash = _hash("admin1234")

    # Check if already exists
    rows = db.execute_query(
        "SELECT user_id FROM users WHERE LOWER(username) = LOWER(?)", (username,)
    )
    if rows:
        uid = int(rows[0]["user_id"])
        print(f"  ✓ Default admin user already exists (id={uid})")
        return uid

    rows = db.execute_query(
        """INSERT INTO users (username, email, role_id, password_hash, created_at)
           VALUES (?, ?, 1, ?, ?) RETURNING user_id""",
        (username, email, password_hash, _now()),
    )
    if rows:
        uid = int(rows[0]["user_id"])
    else:
        r = db.execute_query(
            "SELECT user_id FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        )
        uid = int(r[0]["user_id"]) if r else 0

    print(f"  ✓ Default admin user created — username: '{username}', password: 'admin1234' (id={uid})")
    return uid


def seed_suppliers(db: DatabaseManager) -> list[int]:
    """Insert demo suppliers. Returns list of supplier_ids."""
    suppliers = [
        ("Textile Hub India",  "contact@textilehub.in",  "+91-11-2345-6789", "Sector 14, New Delhi"),
        ("LogiPack Solutions", "supply@logipacks.com",   "+91-22-3456-7890", "Andheri East, Mumbai"),
        ("StatioPro Pvt Ltd",  "orders@statiopro.co.in", "+91-80-4567-8901", "Koramangala, Bengaluru"),
    ]
    ids = []
    for name, email, phone, address in suppliers:
        existing = db.execute_query(
            "SELECT supplier_id FROM suppliers WHERE name = ?", (name,)
        )
        if existing:
            ids.append(int(existing[0]["supplier_id"]))
            continue
        rows = db.execute_query(
            """INSERT INTO suppliers (name, contact_email, phone, address)
               VALUES (?, ?, ?, ?) RETURNING supplier_id""",
            (name, email, phone, address),
        )
        if rows:
            ids.append(int(rows[0]["supplier_id"]))
        else:
            r = db.execute_query(
                "SELECT supplier_id FROM suppliers WHERE name = ?", (name,)
            )
            if r:
                ids.append(int(r[0]["supplier_id"]))

    print(f"  ✓ {len(ids)} suppliers seeded")
    return ids


def seed_products(db: DatabaseManager, supplier_ids: list[int]) -> None:
    """Insert demo inventory products."""
    if not supplier_ids or len(supplier_ids) < 3:
        print("  ! Skipping products — not enough suppliers.")
        return

    now = _now()
    products = [
        # (name, sku, category, qty, reorder, price, supplier_idx)
        ("Cotton Fabric Rolls",       "SKU-9921", "Textiles",   420, 50,  "850.00",  0),
        ("Polyester Blend Fabric",    "SKU-9922", "Textiles",   180, 30,  "620.00",  0),
        ("Packaging Box Medium",      "SKU-4412", "Logistics",   35, 40,   "42.50",  1),
        ("Bubble Wrap Roll 50m",      "SKU-4413", "Logistics",  200, 25,   "85.00",  1),
        ("Barcode Label Rolls",       "SKU-1082", "Stationery", 850, 100, "120.00",  2),
        ("A4 Copy Paper 500-sheet",   "SKU-1083", "Stationery", 640,  80,  "45.00",  2),
        ("Industrial Scissors 10pcs", "SKU-7741", "Tools",       22,  10, "375.00",  1),
        ("Cardboard Dividers Pack",   "SKU-4414", "Logistics",   12,  20,  "28.00",  1),
    ]

    inserted = 0
    for name, sku, category, qty, reorder, price, sup_idx in products:
        existing = db.execute_query(
            "SELECT product_id FROM products WHERE sku = ?", (sku,)
        )
        if existing:
            continue
        db.execute_query(
            """INSERT INTO products
               (name, sku, category, quantity, reorder_level, unit_price, supplier_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, sku, category, qty, reorder, price, supplier_ids[sup_idx], now),
        )
        inserted += 1

    print(f"  ✓ {inserted} products seeded (skipped existing)")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Initialise the database and seed all demo data."""
    print("\n🌱  SMOभर — Database Seeder")
    print("=" * 40)

    db = DatabaseManager.get_instance()
    db.connect()

    print("\n[1/6] Seeding permissions...")
    perm_ids = seed_permissions(db)

    print("\n[2/6] Seeding Admin role...")
    seed_admin_role(db, perm_ids)

    print("\n[3/6] Seeding Manager role...")
    seed_manager_role(db, perm_ids)

    print("\n[4/6] Seeding Supervisor role...")
    seed_supervisor_role(db, perm_ids)

    print("\n[5/6] Seeding default admin user...")
    seed_default_admin_user(db)

    print("\n[6/6] Seeding suppliers and products...")
    supplier_ids = seed_suppliers(db)
    seed_products(db, supplier_ids)

    print("\n" + "=" * 40)
    print("✅  Seeding complete!\n")
    print("   Default login credentials:")
    print("   Username : admin")
    print("   Password : admin1234")
    print("   Role     : Admin (all permissions)\n")


if __name__ == "__main__":
    main()
