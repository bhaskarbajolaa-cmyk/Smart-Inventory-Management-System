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

    @patch("inventory.persistence.adapters.postgresql.psycopg.connect")
    def test_execute_query_reconnects_on_broken_connection(self, connect: Mock) -> None:
        adapter = PostgreSQLAdapter()
        config = SimpleNamespace(
            connection_string="postgresql://user:pass@db.example/inventory",
            timeout=30,
            sslmode="require",
        )
        adapter._config = config

        broken_cursor = MagicMock()
        from psycopg import OperationalError
        broken_cursor.execute.side_effect = OperationalError("consuming input failed: server closed the connection unexpectedly")
        broken_conn = MagicMock()
        broken_conn.cursor.return_value.__enter__.return_value = broken_cursor
        adapter._connection = broken_conn

        recovered_cursor = MagicMock()
        recovered_cursor.description = ("id",)
        recovered_cursor.fetchall.return_value = [{"id": 1}]
        recovered_conn = MagicMock()
        recovered_conn.cursor.return_value.__enter__.return_value = recovered_cursor
        connect.return_value = recovered_conn

        rows = adapter.execute_query("SELECT id FROM products WHERE id = ?", (1,))
        self.assertEqual(rows, [{"id": 1}])
        self.assertEqual(connect.call_count, 1)

    @patch("inventory.persistence.adapters.postgresql.psycopg.connect")
    def test_keepalive_settings_included_in_connection_options(self, connect: Mock) -> None:
        adapter = PostgreSQLAdapter()
        config = SimpleNamespace(
            connection_string="postgresql://user:pass@db.example/inventory",
            timeout=15,
            sslmode="require",
        )
        adapter.connect(config)

        self.assertEqual(connect.call_args.kwargs["keepalives"], 1)
        self.assertEqual(connect.call_args.kwargs["keepalives_idle"], 30)
        self.assertEqual(connect.call_args.kwargs["keepalives_interval"], 10)
        self.assertEqual(connect.call_args.kwargs["keepalives_count"], 5)


if __name__ == "__main__":
    unittest.main()
