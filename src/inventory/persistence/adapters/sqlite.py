"""SQLite database adapter — wraps the standard library ``sqlite3`` module."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from typing import Any

from inventory.persistence.adapters.base import DatabaseAdapter


class SQLiteAdapter(DatabaseAdapter):
    """Concrete adapter for SQLite using ``sqlite3``.

    * Uses ``isolation_level=None`` (autocommit) so that ``BEGIN`` / ``COMMIT``
      / ``ROLLBACK`` are issued explicitly by the adapter methods.
    * Applies WAL journal mode for better concurrent read throughput (skipped
      for in-memory databases where WAL is not supported).
    * Wraps all operations in an ``RLock`` so multiple threads can share the
      same singleton ``DatabaseManager`` safely — SQLite serialises writes
      anyway, so this is both correct and sufficient.
    """

    def __init__(self) -> None:
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._in_transaction: bool = False
        self.last_inserted_id: int | None = None
        self.last_rowcount: int | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def in_transaction(self) -> bool:
        return self._in_transaction

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self, config: Any) -> None:
        """Open a SQLite connection and configure PRAGMA settings."""
        with self._lock:
            if self._connection is not None:
                return
            self._connection = sqlite3.connect(
                config.db_name,
                isolation_level=None,  # autocommit; we manage transactions ourselves
                check_same_thread=getattr(config, "check_same_thread", False),
                timeout=getattr(config, "timeout", 30.0),
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            if config.db_name != ":memory:":
                try:
                    self._connection.execute("PRAGMA journal_mode = WAL")
                except Exception:
                    pass
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._in_transaction = False

    def close(self) -> None:
        """Close the SQLite connection and release the lock."""
        with self._lock:
            if self._connection is not None:
                try:
                    if self._in_transaction:
                        self._safe_rollback()
                    self._connection.close()
                finally:
                    self._connection = None
                    self._in_transaction = False

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> list[sqlite3.Row]:
        """Execute a parameterised query and return ``sqlite3.Row`` objects.

        ``sqlite3.Row`` supports both ``row["column_name"]`` and ``row[index]``
        access, keeping backwards compatibility with any existing index-based
        access patterns in tests.
        """
        with self._lock:
            if self._connection is None:
                raise ConnectionError("SQLite connection is not established.")
            cursor = self._connection.cursor()
            try:
                if params is not None:
                    cursor.execute(sql, tuple(params))
                else:
                    cursor.execute(sql)
                self.last_inserted_id = cursor.lastrowid
                self.last_rowcount = cursor.rowcount
                return cursor.fetchall()
            finally:
                cursor.close()

    def execute_schema(self, schema_sql: str) -> None:
        """Execute a DDL script using ``executescript`` (handles multiple statements)."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("SQLite connection is not established.")
            self._connection.executescript(schema_sql)

    # ------------------------------------------------------------------
    # Transaction management
    # ------------------------------------------------------------------

    def begin_transaction(self) -> None:
        """Issue ``BEGIN`` if no transaction is currently open."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("SQLite connection is not established.")
            if not self._in_transaction:
                self._connection.execute("BEGIN")
                self._in_transaction = True

    def commit(self) -> None:
        """Commit the open transaction."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("SQLite connection is not established.")
            if self._in_transaction:
                self._connection.commit()
                self._in_transaction = False

    def rollback(self) -> None:
        """Roll back the open transaction."""
        with self._lock:
            if self._connection is None:
                raise ConnectionError("SQLite connection is not established.")
            if self._in_transaction:
                self._safe_rollback()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_rollback(self) -> None:
        """Rollback without acquiring the lock (caller holds it already)."""
        try:
            self._connection.rollback()  # type: ignore[union-attr]
        finally:
            self._in_transaction = False
