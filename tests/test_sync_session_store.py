from __future__ import annotations

import sqlite3
import threading
import unittest

from sync_backend.sync_core import (
    cleanup_stale_sessions,
    fetch_session_row,
    row_to_session_payload,
    touch_session,
)


class TestSyncSessionStore(unittest.TestCase):
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
            CREATE TABLE locks (
              project_id TEXT NOT NULL,
              path TEXT NOT NULL,
              token TEXT NOT NULL,
              username TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        return conn

    def test_cleanup_stale_sessions_removes_old_sessions_and_orphan_locks(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        now_ms = 2_000_000
        ttl = 900

        conn.execute(
            "INSERT INTO sessions (token, project_id, username, role, is_admin, active_file, created_at, last_seen) VALUES ('fresh', 'p', 'u', 'user', 0, NULL, ?, ?)",
            (now_ms - 1000, now_ms - 1000),
        )
        conn.execute(
            "INSERT INTO sessions (token, project_id, username, role, is_admin, active_file, created_at, last_seen) VALUES ('stale', 'p', 'u', 'user', 0, NULL, ?, ?)",
            (now_ms - 2_000_000, now_ms - 2_000_000),
        )
        conn.execute("INSERT INTO locks (project_id, path, token, username, updated_at) VALUES ('p', 'a', 'fresh', 'u', ?)", (now_ms,))
        conn.execute("INSERT INTO locks (project_id, path, token, username, updated_at) VALUES ('p', 'b', 'ghost', 'u', ?)", (now_ms,))
        conn.commit()

        cleanup_stale_sessions(conn=conn, db_lock=lock, now_ms=now_ms, session_ttl_seconds=ttl)

        self.assertIsNotNone(fetch_session_row(conn=conn, token="fresh"))
        self.assertIsNone(fetch_session_row(conn=conn, token="stale"))
        row = conn.execute("SELECT COUNT(*) AS c FROM locks").fetchone()
        self.assertEqual(int(row["c"]), 1)

    def test_touch_session_updates_last_seen(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        conn.execute(
            "INSERT INTO sessions (token, project_id, username, role, is_admin, active_file, created_at, last_seen) VALUES ('tok', 'p', 'u', 'user', 0, NULL, 1, 1)"
        )
        conn.commit()

        touch_session(conn=conn, db_lock=lock, token="tok", now_ms=12345)
        row = fetch_session_row(conn=conn, token="tok")
        assert row is not None
        self.assertEqual(int(row["last_seen"]), 12345)

    def test_row_to_session_payload(self) -> None:
        conn = self._make_conn()
        conn.execute(
            "INSERT INTO sessions (token, project_id, username, role, is_admin, active_file, created_at, last_seen) VALUES ('tok', 'p', 'u', '', 1, 'x.txt', 1, 1)"
        )
        conn.commit()

        row = fetch_session_row(conn=conn, token="tok")
        assert row is not None
        payload = row_to_session_payload(row)
        self.assertEqual(payload["token"], "tok")
        self.assertEqual(payload["project_id"], "p")
        self.assertEqual(payload["username"], "u")
        self.assertEqual(payload["role"], "user")
        self.assertTrue(payload["is_admin"])
        self.assertEqual(payload["active_file"], "x.txt")


if __name__ == "__main__":
    unittest.main()


