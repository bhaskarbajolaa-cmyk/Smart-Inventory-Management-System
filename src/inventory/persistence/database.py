"""DatabaseManager and storage implementation boundary."""

from collections.abc import Sequence
import sqlite3
import threading
from typing import Any, ClassVar


SCHEMA_SQL = """
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

CREATE TABLE IF NOT EXISTS table_access_grants (
    grant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id INTEGER REFERENCES permissions(permission_id) ON DELETE CASCADE,
    table_name TEXT,
    granted_by INTEGER,
    granted_at TEXT NOT NULL,
    permission_code TEXT NOT NULL
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
    transaction_type TEXT NOT NULL,
    sale_price TEXT,
    customer_ref TEXT,
    sold_by_user_id INTEGER,
    unit_cost TEXT,
    supplier_id INTEGER REFERENCES suppliers(supplier_id),
    ordered_by_user_id INTEGER
);
"""


class DatabaseManager:
    """Singleton entry point for PostgreSQL or the in-memory demo store."""

    _instance: ClassVar["DatabaseManager | None"] = None
    connection_pool: Any

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        db_name: str = "inventory.db",
    ) -> None:
        self.host = host
        self.port = port
        self.db_name = db_name
        self.connection_pool = None
        self._connection: sqlite3.Connection | None = None
        self.in_transaction: bool = False
        self.last_inserted_id: int | None = None
        self.last_rowcount: int | None = None
        self._lock: threading.RLock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        """Return the shared singleton DatabaseManager instance."""
        if cls._instance is None:
            cls._instance = DatabaseManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (primarily for tests)."""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None

    def connect(self) -> "DatabaseManager":
        """Establish database connection, enable foreign keys, and initialize schema."""
        with self._lock:
            if self._connection is None:
                self._connection = sqlite3.connect(
                    self.db_name,
                    isolation_level=None,
                    check_same_thread=False,
                    timeout=30.0,
                )
                self._connection.execute("PRAGMA foreign_keys = ON")
                if self.db_name != ":memory:":
                    try:
                        self._connection.execute("PRAGMA journal_mode = WAL")
                    except Exception:
                        pass
                self._connection.execute("PRAGMA busy_timeout = 5000")
                self._connection.row_factory = sqlite3.Row
                self.in_transaction = False
                self.initialize_schema()
        return self

    def initialize_schema(self) -> None:
        """Execute table creation DDL for core entities if not already created."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("Database connection is not established.")
            self._connection.executescript(SCHEMA_SQL)

    def execute_query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> list[sqlite3.Row]:
        """Execute a parameterized query and return its result set."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("Database connection is not established.")
            cursor = self._connection.cursor()
            try:
                if params is not None:
                    cursor.execute(sql, tuple(params))
                else:
                    cursor.execute(sql)
                self.last_inserted_id = cursor.lastrowid
                self.last_rowcount = cursor.rowcount
                result = cursor.fetchall()
                return result
            finally:
                cursor.close()

    def begin_transaction(self) -> "DatabaseManager":
        """Start a transaction on the current connection/context."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("Database connection is not established.")
            if not self.in_transaction:
                self._connection.execute("BEGIN")
                self.in_transaction = True
        return self

    def commit(self) -> None:
        """Commit the current transaction."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("Database connection is not established.")
            if self.in_transaction:
                self._connection.commit()
                self.in_transaction = False

    def rollback(self) -> None:
        """Roll back the current transaction after a failed operation."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("Database connection is not established.")
            if self.in_transaction:
                self._connection.rollback()
                self.in_transaction = False

    def close(self) -> None:
        """Close the database connection and release resources."""
        with self._lock:
            if self._connection is not None:
                try:
                    if self.in_transaction:
                        self.rollback()
                    self._connection.close()
                finally:
                    self._connection = None
                    self.in_transaction = False
