from __future__ import annotations

import sqlite3
import unittest

from sync_backend.sync_core import (
    build_search_clause,
    serialize_db_rows,
    table_columns,
    table_exists,
    validate_table_name,
)


class TestSyncAdminDbQuery(unittest.TestCase):
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE demo (id INTEGER, name TEXT, blob BLOB)")
        conn.execute("INSERT INTO demo VALUES (1, 'abc', x'0102')")
        conn.commit()
        return conn

    def test_validate_and_exists_and_columns(self) -> None:
        conn = self._conn()
        self.assertTrue(validate_table_name("demo"))
        self.assertFalse(validate_table_name("bad-name"))
        self.assertTrue(table_exists(conn=conn, table="demo"))
        self.assertIn("name", table_columns(conn=conn, table="demo"))

    def test_build_search_clause_and_serialize(self) -> None:
        conn = self._conn()
        where, params = build_search_clause(search_text="abc", search_column="name", columns=["id", "name"])
        self.assertIn("name", where)
        self.assertEqual(len(params), 1)
        rows = conn.execute(f'SELECT * FROM "demo"{where}', tuple(params)).fetchall()
        out = serialize_db_rows(rows)
        self.assertEqual(out[0]["name"], "abc")
        self.assertEqual(out[0]["blob"]["type"], "bytes")


if __name__ == "__main__":
    unittest.main()


