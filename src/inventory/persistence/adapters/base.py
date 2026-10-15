"""Abstract database adapter contract shared by all backend implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any


class DatabaseAdapter(ABC):
    """Protocol-level contract for database backends (SQLite, PostgreSQL, …).

    Every concrete adapter must:
    - maintain its own connection and ``in_transaction`` flag
    - return rows as objects that support ``row["column_name"]`` access
    - raise ``ConnectionError`` when a method is called before ``connect()``
    """

    last_inserted_id: int | None = None
    last_rowcount: int | None = None

    @property
    @abstractmethod
    def in_transaction(self) -> bool:
        """Whether a transaction is currently open on this adapter."""
        ...

    @abstractmethod
    def connect(self, config: Any) -> None:
        """Establish the backend connection and apply initial settings."""
        ...

    @abstractmethod
    def execute_query(self, sql: str, params: Sequence[Any] | None = None) -> list[Any]:
        """Execute a parameterised query and return its result rows.

        Rows must support ``row["column_name"]`` access.
        """
        ...

    @abstractmethod
    def execute_schema(self, schema_sql: str) -> None:
        """Execute a multi-statement DDL script (``CREATE TABLE IF NOT EXISTS …``)."""
        ...

    @abstractmethod
    def begin_transaction(self) -> None:
        """Open a transaction if one is not already open."""
        ...

    @abstractmethod
    def commit(self) -> None:
        """Commit the open transaction."""
        ...

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the open transaction."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release the connection and clean up resources."""
        ...
