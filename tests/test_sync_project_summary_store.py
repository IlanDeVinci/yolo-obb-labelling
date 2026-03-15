from __future__ import annotations

import sqlite3
import unittest

from sync_backend.sync_core import fetch_project_summary_rows


class TestSyncProjectSummaryStore(unittest.TestCase):
    def test_fetch_project_summary_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE users (project_id TEXT, username TEXT);
            CREATE TABLE files (project_id TEXT, path TEXT, deleted INTEGER);
            CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, path TEXT, username TEXT, created_at INTEGER);
            INSERT INTO users VALUES ('p','u1');
            INSERT INTO files VALUES ('p','a',0);
            INSERT INTO changes (project_id,path,username,created_at) VALUES ('p','a','u1',10);
            """
        )
        out = fetch_project_summary_rows(conn=conn, project_id="p")
        self.assertEqual(out["users"], 1)
        self.assertEqual(out["files"], 1)
        self.assertEqual(out["changes"], 1)
        self.assertEqual(out["latestChange"]["path"], "a")


if __name__ == "__main__":
    unittest.main()


