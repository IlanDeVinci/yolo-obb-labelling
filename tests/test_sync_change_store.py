from __future__ import annotations

import sqlite3
import unittest

from sync_backend.sync_core import (
    delete_image_status_for_path,
    insert_change_record,
    max_change_seq,
    touch_session_last_seen,
    upsert_file_record,
)


class TestSyncChangeStore(unittest.TestCase):
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
            CREATE TABLE files (
              project_id TEXT NOT NULL,
              path TEXT NOT NULL,
              deleted INTEGER NOT NULL,
              mtime_ms INTEGER NOT NULL,
              sha1 TEXT NOT NULL,
              content_base64 TEXT NOT NULL,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY (project_id, path)
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
              status TEXT NOT NULL
            );
            """
        )
        conn.commit()
        return conn

    def test_touch_session_last_seen(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO sessions VALUES ('t', 'p', 'u', 'user', 0, NULL, 1, 1)")
        conn.commit()

        touch_session_last_seen(conn=conn, token="t", now_ms=123)
        row = conn.execute("SELECT last_seen FROM sessions WHERE token='t'").fetchone()
        self.assertEqual(int(row["last_seen"]), 123)

    def test_upsert_and_insert_change(self) -> None:
        conn = self._make_conn()
        upsert_file_record(
            conn=conn,
            project_id="p",
            path="images/a.jpg",
            deleted=False,
            mtime_ms=10,
            sha1="s",
            content_base64="abc",
            updated_at=20,
        )
        insert_change_record(
            conn=conn,
            project_id="p",
            username="u",
            source_token="t",
            path="images/a.jpg",
            deleted=False,
            mtime_ms=10,
            sha1="s",
            content_base64="abc",
            created_at=20,
        )
        conn.commit()

        seq = max_change_seq(conn=conn, project_id="p")
        self.assertGreaterEqual(seq, 1)

    def test_delete_image_status_for_path(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO image_status VALUES ('p', 'a.jpg', 'completed')")
        conn.commit()

        ok = delete_image_status_for_path(conn=conn, project_id="p", path="images/a.jpg")
        self.assertTrue(ok)
        row = conn.execute("SELECT COUNT(*) AS c FROM image_status").fetchone()
        self.assertEqual(int(row["c"]), 0)


if __name__ == "__main__":
    unittest.main()


