from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from inventory.persistence.adapters.postgresql import PostgreSQLAdapter


class PostgreSQLAdapterTests(unittest.TestCase):
    @patch("inventory.persistence.adapters.postgresql.psycopg.connect")
    def test_connection_url_is_passed_with_tls_defaults(self, connect: Mock) -> None:
        adapter = PostgreSQLAdapter()
        config = SimpleNamespace(
            connection_string="postgresql://user:pass@db.example/inventory",
            timeout=9,
            sslmode="require",
        )

        adapter.connect(config)

        self.assertEqual(
            connect.call_args.args[0],
            "postgresql://user:pass@db.example/inventory",
        )
        self.assertEqual(connect.call_args.kwargs["sslmode"], "require")
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 9)

    @patch("inventory.persistence.adapters.postgresql.psycopg.connect")
    def test_url_sslmode_is_not_overridden(self, connect: Mock) -> None:
        adapter = PostgreSQLAdapter()
        config = SimpleNamespace(
            connection_string=(
                "postgresql://user:pass@db.example/inventory?sslmode=verify-full"
            ),
            timeout=30,
            sslmode="require",
        )

        adapter.connect(config)

        self.assertNotIn("sslmode", connect.call_args.kwargs)

    def test_returning_captures_any_named_id_column(self) -> None:
        adapter = PostgreSQLAdapter()
        cursor = MagicMock()
        cursor.description = ("transaction_id",)
        cursor.fetchall.return_value = [{"transaction_id": 37}]
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        adapter._connection = connection

        rows = adapter.execute_query(
            "INSERT INTO stock_transactions (product_id) VALUES (?) "
            "RETURNING transaction_id",
            (1,),
        )

        self.assertEqual(rows[0]["transaction_id"], 37)
        self.assertEqual(adapter.last_inserted_id, 37)
        cursor.execute.assert_called_once_with(
            "INSERT INTO stock_transactions (product_id) VALUES (%s) "
            "RETURNING transaction_id",
            (1,),
        )

    def test_schema_errors_are_propagated(self) -> None:
        adapter = PostgreSQLAdapter()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("DDL rejected")
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        adapter._connection = connection

        with self.assertRaisesRegex(RuntimeError, "DDL rejected"):
            adapter.execute_schema("CREATE TABLE example (id INTEGER);")


if __name__ == "__main__":
    unittest.main()
