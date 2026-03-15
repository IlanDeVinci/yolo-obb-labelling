from __future__ import annotations

import sqlite3
import unittest

from sync_backend.sync_core import fetch_recent_changes, map_recent_changes


class TestSyncRecentChangesStore(unittest.TestCase):
    def test_fetch_and_map_recent_changes(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, username TEXT, path TEXT, deleted INTEGER, mtime_ms INTEGER, created_at INTEGER);
            INSERT INTO changes (project_id,username,path,deleted,mtime_ms,created_at) VALUES ('p','u','a',0,1,2);
            """
        )
        rows = fetch_recent_changes(conn=conn, project_id="p", limit=5)
        mapped = map_recent_changes(rows)
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0]["path"], "a")


if __name__ == "__main__":
    unittest.main()


