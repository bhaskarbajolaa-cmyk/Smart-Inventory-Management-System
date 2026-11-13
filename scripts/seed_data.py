"""Seed the database with initial roles, permissions, users, suppliers, and products.

Run from the project root (where pyproject.toml lives):

    python scripts/seed_data.py

Or via the installed entry-point after `pip install -e .[dev]`:

    python -m inventory.app.main --mode cli   # starts app (builds DB first)
    python scripts/seed_data.py               # then seeds

The seeder is safe to re-run — it uses portable ON CONFLICT DO NOTHING
statements so existing data is not duplicated.
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


def _ensure_global_grant(db: DatabaseManager, role_id: int, permission_id: int) -> None:
    existing = db.execute_query(
        """SELECT grant_id FROM table_access_grants
           WHERE role_id = ? AND permission_id = ? AND table_name IS NULL""",
        (role_id, permission_id),
    )
    if not existing:
        db.execute_query(
            """INSERT INTO table_access_grants
               (role_id, permission_id, table_name, granted_by, granted_at)
               VALUES (?, ?, NULL, 0, ?)""",
            (role_id, permission_id, _now()),
        )


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
            "INSERT INTO permissions (code, description) VALUES (?, ?) ON CONFLICT DO NOTHING",
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
        """INSERT INTO roles
           (role_id, role_name, created_by, is_system_default, default_job_priority)
           VALUES (1, 'Admin', 0, 1, 1) ON CONFLICT DO NOTHING""",
    )
    rows = db.execute_query("SELECT role_id FROM roles WHERE role_id = 1")
    role_id = int(rows[0]["role_id"])

    # Grant every permission globally (table_name = NULL)
    for code, pid in perm_ids.items():
        _ensure_global_grant(db, role_id, pid)

    print(f"  ✓ Admin role (id=1) seeded with {len(perm_ids)} global grants")
    return role_id


def seed_manager_role(db: DatabaseManager, perm_ids: dict[str, int]) -> int:
    """Insert the Manager role with VIEW + APPEND + EDIT. Returns role_id."""
    db.execute_query(
        """INSERT INTO roles
           (role_id, role_name, created_by, is_system_default, default_job_priority)
           VALUES (2, 'Manager', 0, 1, 2) ON CONFLICT DO NOTHING""",
    )
    rows = db.execute_query("SELECT role_id FROM roles WHERE role_id = 2")
    role_id = int(rows[0]["role_id"])

    manager_perms = ["VIEW", "APPEND", "EDIT"]
    for code in manager_perms:
        _ensure_global_grant(db, role_id, perm_ids[code])

    print(f"  ✓ Manager role (id=2) seeded with {manager_perms}")
    return role_id


def seed_supervisor_role(db: DatabaseManager, perm_ids: dict[str, int]) -> int:
    """Insert the Supervisor role with VIEW only. Returns role_id."""
    db.execute_query(
        """INSERT INTO roles
           (role_id, role_name, created_by, is_system_default, default_job_priority)
           VALUES (3, 'Supervisor', 0, 1, 3) ON CONFLICT DO NOTHING""",
    )
    if db.config.backend == "postgresql":
        db.execute_query(
            """SELECT setval(
                   pg_get_serial_sequence('roles', 'role_id'),
                   (SELECT COALESCE(MAX(role_id), 1) FROM roles)
               )"""
        )
    rows = db.execute_query("SELECT role_id FROM roles WHERE role_id = 3")
    role_id = int(rows[0]["role_id"])

    _ensure_global_grant(db, role_id, perm_ids["VIEW"])

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


def seed_transactions(db: DatabaseManager) -> None:
    """Insert demo historical sales and purchases for analytics if table is empty."""
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)

    existing = db.execute_query("SELECT COUNT(*) AS cnt FROM stock_transactions")
    if existing and int(existing[0]["cnt"]) > 0:
        print("  ! Skipping transactions — transactions already exist.")
        return

    prods = db.execute_query("SELECT product_id, sku, unit_price, supplier_id FROM products")
    if not prods:
        return

    user_rows = db.execute_query("SELECT user_id FROM users WHERE username = 'admin'")
    user_id = int(user_rows[0]["user_id"]) if user_rows else 1

    prod_map = {row["sku"]: row for row in prods}

    # Deterministic simulation dataset over 60 days
    # Patterns: High velocity: SKU-9921, SKU-1082; Moderate: SKU-9922, SKU-1083; Low: SKU-7741, SKU-4414
    txns = []

    # Initial restock purchases at day -55 and -25
    for sku, p_data in prod_map.items():
        price = float(p_data["unit_price"])
        cost = round(price * 0.65, 2)
        sup_id = p_data["supplier_id"] or 1
        pid = p_data["product_id"]

        # 2 restocks
        for day_ago in (55, 25):
            ts = (now - timedelta(days=day_ago, hours=4)).isoformat()
            txns.append((
                pid, user_id, 80, ts, "completed", "purchase",
                None, None, None, str(cost), sup_id, user_id
            ))

    # Daily and periodic sales across the 60-day window
    for d in range(58, 0, -1):
        ts_base = now - timedelta(days=d)

        # SKU-9921 (Cotton Fabric Rolls - fast moving, high value)
        if d % 2 == 0 or d % 3 == 0:
            p = prod_map.get("SKU-9921")
            if p:
                qty = 8 + (d % 7) * 2
                ts = (ts_base.replace(hour=10 + (d % 6), minute=15)).isoformat()
                txns.append((
                    p["product_id"], user_id, qty, ts, "completed", "sale",
                    str(p["unit_price"]), f"CUST-TEX-{1000 + d}", user_id, None, None, None
                ))

        # SKU-1082 (Barcode Label Rolls - fast moving, stationery)
        if d % 2 != 0:
            p = prod_map.get("SKU-1082")
            if p:
                qty = 15 + (d % 5) * 3
                ts = (ts_base.replace(hour=14 + (d % 4), minute=30)).isoformat()
                txns.append((
                    p["product_id"], user_id, qty, ts, "completed", "sale",
                    str(p["unit_price"]), f"CUST-OFF-{2000 + d}", user_id, None, None, None
                ))

        # SKU-9922 (Polyester Blend Fabric - moderate)
        if d % 4 == 0:
            p = prod_map.get("SKU-9922")
            if p:
                qty = 5 + (d % 4)
                ts = (ts_base.replace(hour=11, minute=45)).isoformat()
                txns.append((
                    p["product_id"], user_id, qty, ts, "completed", "sale",
                    str(p["unit_price"]), f"CUST-TEX-{3000 + d}", user_id, None, None, None
                ))

        # SKU-1083 (A4 Copy Paper - moderate)
        if d % 3 == 0:
            p = prod_map.get("SKU-1083")
            if p:
                qty = 10 + (d % 6)
                ts = (ts_base.replace(hour=16, minute=10)).isoformat()
                txns.append((
                    p["product_id"], user_id, qty, ts, "completed", "sale",
                    str(p["unit_price"]), f"CUST-OFF-{4000 + d}", user_id, None, None, None
                ))

        # SKU-4412 (Packaging Box Medium - low/moderate)
        if d % 5 == 0:
            p = prod_map.get("SKU-4412")
            if p:
                qty = 4 + (d % 3)
                ts = (ts_base.replace(hour=12, minute=0)).isoformat()
                txns.append((
                    p["product_id"], user_id, qty, ts, "completed", "sale",
                    str(p["unit_price"]), f"CUST-LOG-{5000 + d}", user_id, None, None, None
                ))

        # SKU-7741 (Industrial Scissors 10pcs - slow moving)
        if d in (52, 41, 33, 21, 9, 3):
            p = prod_map.get("SKU-7741")
            if p:
                ts = (ts_base.replace(hour=15, minute=20)).isoformat()
                txns.append((
                    p["product_id"], user_id, 2, ts, "completed", "sale",
                    str(p["unit_price"]), f"CUST-TOOL-{6000 + d}", user_id, None, None, None
                ))

    # Insert transactions
    sql = (
        "INSERT INTO stock_transactions "
        "(product_id, user_id, quantity, timestamp, status, transaction_type, "
        "sale_price, customer_ref, sold_by_user_id, unit_cost, supplier_id, ordered_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    for row in txns:
        db.execute_query(sql, row)

    print(f"  ✓ {len(txns)} transactions seeded (sales & restock purchases over 60 days)")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Initialise the database and seed all demo data."""
    print("\n🌱  SMOभर — Database Seeder")
    print("=" * 40)

    db = DatabaseManager.get_instance()
    db.connect()

    print("\n[1/7] Seeding permissions...")
    perm_ids = seed_permissions(db)

    print("\n[2/7] Seeding Admin role...")
    seed_admin_role(db, perm_ids)

    print("\n[3/7] Seeding Manager role...")
    seed_manager_role(db, perm_ids)

    print("\n[4/7] Seeding Supervisor role...")
    seed_supervisor_role(db, perm_ids)

    print("\n[5/7] Seeding default admin user...")
    seed_default_admin_user(db)

    print("\n[6/7] Seeding suppliers and products...")
    supplier_ids = seed_suppliers(db)
    seed_products(db, supplier_ids)

    print("\n[7/7] Seeding demo transaction history for analytics...")
    seed_transactions(db)

    print("\n" + "=" * 40)
    print("✅  Seeding complete!\n")
    print("   Default login credentials:")
    print("   Username : admin")
    print("   Password : admin1234")
    print("   Role     : Admin (all permissions)\n")


if __name__ == "__main__":
    main()

