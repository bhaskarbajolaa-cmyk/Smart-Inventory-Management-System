-- Database schema for Smart Inventory Management System
-- This file contains all table definitions for the inventory database

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    phone TEXT NOT NULL,
    address TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sku TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 0,
    unit_price TEXT NOT NULL,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    role_id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name TEXT NOT NULL UNIQUE,
    created_by INTEGER,
    is_system_default INTEGER NOT NULL DEFAULT 0,
    default_job_priority INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS permissions (
    permission_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    description TEXT
);

-- permission_code column removed: derive it from a JOIN with permissions
-- to avoid denormalisation and stale copies.
CREATE TABLE IF NOT EXISTS table_access_grants (
    grant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    table_name TEXT,
    granted_by INTEGER,
    granted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    role_id INTEGER REFERENCES roles(role_id),
    password_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_token TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
    roles TEXT,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_transactions (
    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    quantity INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('sale', 'purchase')),
    -- Sale-specific fields (NULL for purchases)
    sale_price TEXT,
    customer_ref TEXT,
    sold_by_user_id INTEGER,
    -- Purchase-specific fields (NULL for sales)
    unit_cost TEXT,
    supplier_id INTEGER REFERENCES suppliers(supplier_id),
    ordered_by_user_id INTEGER
);

-- ============================================================
-- Custom Tables feature
-- ============================================================

CREATE TABLE IF NOT EXISTS custom_table_definitions (
    table_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT NOT NULL UNIQUE,
    owner_id    INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_field_definitions (
    field_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id      INTEGER NOT NULL
                      REFERENCES custom_table_definitions(table_id) ON DELETE CASCADE,
    field_name    TEXT NOT NULL,
    data_type     TEXT NOT NULL,
    required      INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (table_id, field_name)
);

CREATE TABLE IF NOT EXISTS custom_records (
    record_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id   INTEGER NOT NULL
                   REFERENCES custom_table_definitions(table_id) ON DELETE CASCADE,
    data       TEXT NOT NULL,   -- JSON-encoded record payload
    created_at TEXT NOT NULL
);
