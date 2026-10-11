"""DatabaseManager and storage implementation boundary."""

import sqlite3
from typing import Any, ClassVar


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
        # TODO: Store configuration and prepare connection-pool/store state.
        self.host = host
        self.port = port
        self.db_name = db_name
        self.connection_pool = None
        # for in database connection 
        self._connection: sqlite3.Connection | None = None
        self.in_transaction: bool = False

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        # TODO: Create exactly one configured manager and return it on later calls.
        if(cls._instance is None):
            cls._instance = DatabaseManager()
        return cls._instance

    def connect(self) -> Any:
        # TODO: Initialize PostgreSQL connections or the in-memory backing store.
        self._connection = sqlite3.connect(self.db_name,isolation_level=None)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.row_factory = sqlite3.Row
        self.in_transaction = False
        return self

    def execute_query(self, sql: str, params: tuple[Any, ...] | None = None) -> Any:
        # TODO: Execute a parameterized query and return its result set.
        if(self._connection is None):
            raise Exception("Database connection is not established.")
        cursor  = self._connection.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        result = cursor.fetchall()
        cursor.close()
        return result

    def begin_transaction(self) -> Any:
        # TODO: Start a transaction on the current connection/context.
        if(self._connection is None):
            raise Exception("Database connection is not established.")
        self._connection.execute("BEGIN")
        self.in_transaction = True
        return self

    def commit(self) -> None:
        # TODO: Commit the current transaction.
        if(self._connection is None):
            raise Exception("Database connection is not established.")
        self._connection.commit()
        self.in_transaction = False
        return self

    def rollback(self) -> None:
        # TODO: Roll back the current transaction after a failed operation.
        if(self._connection is None):
            raise Exception("Database connection is not established.")
        self._connection.rollback()
        self.in_transaction = False
        return self
