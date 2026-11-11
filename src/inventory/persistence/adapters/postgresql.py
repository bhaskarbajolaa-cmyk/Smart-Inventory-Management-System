"""PostgreSQL database adapter — uses ``psycopg`` (psycopg3).

PostgreSQL support is included in the project runtime dependencies. Set
``DatabaseConfig.backend = "postgresql"`` and supply a PostgreSQL connection
URL or the individual connection settings.

Placeholder translation
-----------------------
Repositories write all SQL with SQLite-style ``?`` placeholders.  This
adapter automatically converts them to the ``%s`` style that psycopg expects,
so no repository SQL needs to change when switching backends.

Schema compatibility
--------------------
The database manager supplies a PostgreSQL-specific schema for this adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from inventory.persistence.adapters.base import DatabaseAdapter

# ---------------------------------------------------------------------------
# Optional psycopg3 import — guarded so that the package can be imported even
# when the driver is not installed.  An ImportError is raised only when the
# PostgreSQLAdapter is actually *used*.
# ---------------------------------------------------------------------------
try:
    import psycopg
    from psycopg.rows import dict_row as _psycopg_dict_row
    _PSYCOPG_AVAILABLE = True
except ImportError:
    _PSYCOPG_AVAILABLE = False


def _to_pg_sql(sql: str) -> str:
    """Replace SQLite ``?`` placeholders with PostgreSQL ``%s``."""
    return sql.replace("?", "%s")


class PostgreSQLAdapter(DatabaseAdapter):
    """Concrete adapter for PostgreSQL using psycopg3.

    Rows are returned as plain ``dict`` objects, which support the same
    ``row["column_name"]`` access pattern as ``sqlite3.Row``.
    """

    def __init__(self) -> None:
        if not _PSYCOPG_AVAILABLE:
            raise ImportError(
                "psycopg is not installed. "
                "Run: pip install \"psycopg[binary]>=3.1\""
            )
        self._connection: Any | None = None  # psycopg.Connection[dict]
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
        """Open a psycopg3 connection using host/port/db_name from config."""
        if not _PSYCOPG_AVAILABLE:
            raise ImportError(
                "psycopg is not installed. "
                "Run: pip install \"psycopg[binary]>=3.1\""
            )
        connection_string = getattr(config, "connection_string", "")
        if connection_string:
            connection_options: dict[str, Any] = {
                "row_factory": _psycopg_dict_row,
                "autocommit": True,
                "connect_timeout": int(getattr(config, "timeout", 30)),
            }
            if "sslmode=" not in connection_string:
                connection_options["sslmode"] = getattr(config, "sslmode", "require")
            self._connection = psycopg.connect(  # type: ignore[union-attr]
                connection_string,
                **connection_options,
            )
            self._in_transaction = False
            return
        parts = [
            f"host={config.host}",
            f"port={config.port}",
            f"dbname={config.db_name}",
            f"connect_timeout={int(getattr(config, 'timeout', 30))}",
        ]
        if getattr(config, "user", ""):
            parts.append(f"user={config.user}")
        if getattr(config, "password", ""):
            parts.append(f"password={config.password}")

        conninfo = " ".join(parts)
        self._connection = psycopg.connect(  # type: ignore[union-attr]
            conninfo,
            row_factory=_psycopg_dict_row,  # type: ignore[arg-type]
            autocommit=True,
            sslmode=getattr(config, "sslmode", "require"),
        )
        self._in_transaction = False

    def close(self) -> None:
        if self._connection is not None:
            try:
                if self._in_transaction:
                    self.rollback()
                self._connection.close()
            finally:
                self._connection = None
                self._in_transaction = False

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a parameterised query with ``?`` → ``%s`` conversion."""
        if self._connection is None:
            raise ConnectionError("PostgreSQL connection is not established.")
        pg_sql = _to_pg_sql(sql)
        self.last_inserted_id = None
        with self._connection.cursor() as cursor:
            cursor.execute(pg_sql, tuple(params) if params else None)
            self.last_rowcount = cursor.rowcount
            # lastrowid is not available in psycopg3 directly; use RETURNING
            if cursor.description is not None:
                rows: list[dict[str, Any]] = cursor.fetchall()
            else:
                rows = []
            if rows and "RETURNING" in pg_sql.upper():
                self.last_inserted_id = int(next(iter(rows[0].values())))
            return rows

    def execute_schema(self, schema_sql: str) -> None:
        """Execute all statements in the PostgreSQL-specific schema."""
        if self._connection is None:
            raise ConnectionError("PostgreSQL connection is not established.")
        for stmt in schema_sql.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            with self._connection.cursor() as cursor:
                cursor.execute(stmt)

    # ------------------------------------------------------------------
    # Transaction management
    # ------------------------------------------------------------------

    def begin_transaction(self) -> None:
        if self._connection is None:
            raise ConnectionError("PostgreSQL connection is not established.")
        if not self._in_transaction:
            with self._connection.cursor() as cursor:
                cursor.execute("BEGIN")
            self._in_transaction = True

    def commit(self) -> None:
        if self._connection is None:
            raise ConnectionError("PostgreSQL connection is not established.")
        if self._in_transaction:
            with self._connection.cursor() as cursor:
                cursor.execute("COMMIT")
            self._in_transaction = False

    def rollback(self) -> None:
        if self._connection is None:
            raise ConnectionError("PostgreSQL connection is not established.")
        if self._in_transaction:
            try:
                with self._connection.cursor() as cursor:
                    cursor.execute("ROLLBACK")
            finally:
                self._in_transaction = False
