"""DatabaseManager — thin coordinator that delegates all DB work to the active adapter.

Switching between SQLite and PostgreSQL is done by setting
``DatabaseConfig.backend`` to ``"sqlite"`` (default) or ``"postgresql"``.
All repositories and transaction code interact with ``DatabaseManager`` exactly
as before; the adapter change is completely transparent to them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from inventory.persistence.config import (
    DatabaseConfig,
    DEFAULT_DB_NAME,
    DEFAULT_HOST,
    DEFAULT_PORT,
)
from inventory.persistence.adapters.base import DatabaseAdapter


def load_schema() -> str:
    """Load the SQL schema DDL from the sibling ``schema.sql`` file."""
    schema_file = Path(__file__).parent / "schema.sql"
    return schema_file.read_text()


class DatabaseManager:
    """Singleton coordinator that delegates all DB operations to the active adapter.

    Usage (SQLite — default)::

        db = DatabaseManager.get_instance()
        db.connect()

    Usage (PostgreSQL)::

        config = DatabaseConfig(
            backend="postgresql",
            host="localhost", port=5432,
            db_name="inventory", user="inv_user", password="secret",
        )
        db = DatabaseManager(config=config)
        db.connect()
    """

    _instance: ClassVar["DatabaseManager | None"] = None

    def __init__(
        self,
        config: DatabaseConfig | None = None,
        host: str | None = None,
        port: int | None = None,
        db_name: str | None = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = DatabaseConfig(
                host=host or DEFAULT_HOST,
                port=port or DEFAULT_PORT,
                db_name=db_name or DEFAULT_DB_NAME,
            )

        # Convenience aliases kept for backwards-compat with existing code
        self.host = self.config.host
        self.port = self.config.port
        self.db_name = self.config.db_name
        self.connection_pool: Any = None  # reserved for future connection-pool support

        self._adapter: DatabaseAdapter | None = None

    # ------------------------------------------------------------------
    # Singleton helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        """Return the shared singleton ``DatabaseManager`` instance."""
        if cls._instance is None:
            cls._instance = DatabaseManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (primarily for tests)."""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> "DatabaseManager":
        """Create the adapter, open the connection, and initialise the schema."""
        if self._adapter is None:
            self._adapter = self._create_adapter()
            self._adapter.connect(self.config)
            self.initialize_schema()
        return self

    def _create_adapter(self) -> DatabaseAdapter:
        """Instantiate the adapter selected by ``DatabaseConfig.backend``."""
        backend = getattr(self.config, "backend", "sqlite")
        if backend == "postgresql":
            from inventory.persistence.adapters.postgresql import PostgreSQLAdapter
            return PostgreSQLAdapter()
        from inventory.persistence.adapters.sqlite import SQLiteAdapter
        return SQLiteAdapter()

    def initialize_schema(self) -> None:
        """Execute the schema DDL via the active adapter."""
        if self._adapter is None:
            raise ConnectionError("Database connection is not established.")
        self._adapter.execute_schema(load_schema())

    def close(self) -> None:
        """Close the adapter connection and release resources."""
        if self._adapter is not None:
            try:
                self._adapter.close()
            finally:
                self._adapter = None

    # ------------------------------------------------------------------
    # Query delegation
    # ------------------------------------------------------------------

    def execute_query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> list[Any]:
        """Execute a parameterised query and return its result rows."""
        if self._adapter is None:
            raise ConnectionError("Database connection is not established.")
        return self._adapter.execute_query(sql, params)

    # ------------------------------------------------------------------
    # Transaction delegation
    # ------------------------------------------------------------------

    def begin_transaction(self) -> "DatabaseManager":
        """Start a transaction on the current connection."""
        if self._adapter is None:
            raise ConnectionError("Database connection is not established.")
        self._adapter.begin_transaction()
        return self

    def commit(self) -> None:
        """Commit the current transaction."""
        if self._adapter is None:
            raise ConnectionError("Database connection is not established.")
        self._adapter.commit()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        if self._adapter is None:
            raise ConnectionError("Database connection is not established.")
        self._adapter.rollback()

    # ------------------------------------------------------------------
    # Delegated read-only properties
    # ------------------------------------------------------------------

    @property
    def in_transaction(self) -> bool:
        """Whether a transaction is currently open (delegated to adapter)."""
        return self._adapter.in_transaction if self._adapter else False

    @property
    def last_inserted_id(self) -> int | None:
        """Row ID of the most recently inserted row."""
        return self._adapter.last_inserted_id if self._adapter else None

    @property
    def last_rowcount(self) -> int | None:
        """Row count affected by the most recent DML statement."""
        return self._adapter.last_rowcount if self._adapter else None
