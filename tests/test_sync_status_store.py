from __future__ import annotations

import sqlite3
import unittest

from sync_backend.sync_core import (
    latest_change_seq,
    latest_status_seq,
    online_users_count,
)


class TestSyncStatusStore(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE sessions (
              token TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
              role TEXT NOT NULL,
              is_admin INTEGER NOT NULL,
              active_file TEXT,
              created_at INTEGER NOT NULL,
              last_seen INTEGER NOT NULL
            );
            CREATE TABLE changes (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
              source_token TEXT NOT NULL,
              path TEXT NOT NULL,
              deleted INTEGER NOT NULL,
              mtime_ms INTEGER NOT NULL,
              sha1 TEXT NOT NULL,
              content_base64 TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE image_status (
              project_id TEXT NOT NULL,
              image_name TEXT NOT NULL,
              status TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        conn.commit()
        return conn

    def test_status_metrics(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO sessions VALUES ('t1','p','u','user',0,NULL,1,1)")
        conn.execute("INSERT INTO sessions VALUES ('t2','p','v','user',0,NULL,1,1)")
        conn.execute("INSERT INTO changes (project_id,username,source_token,path,deleted,mtime_ms,sha1,content_base64,created_at) VALUES ('p','u','t1','x',0,1,'s','b',1)")
        conn.execute("INSERT INTO image_status VALUES ('p','a.jpg','completed',123)")
        conn.commit()

        self.assertEqual(online_users_count(conn=conn, project_id='p'), 2)
        self.assertGreaterEqual(latest_change_seq(conn=conn, project_id='p'), 1)
        self.assertEqual(latest_status_seq(conn=conn, project_id='p'), 123)


if __name__ == "__main__":
    unittest.main()


