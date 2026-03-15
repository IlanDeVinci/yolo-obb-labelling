from __future__ import annotations

import sqlite3
import threading
import unittest

from sync_backend.sync_core import (
    delete_user_sessions_and_locks,
    logout_session_token,
    owner_count_for_project,
)


class TestSyncUserSessionStore(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE locks (
              project_id TEXT NOT NULL,
              path TEXT NOT NULL,
              token TEXT NOT NULL,
              username TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
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
            CREATE TABLE users (
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              is_admin INTEGER NOT NULL,
              role TEXT NOT NULL,
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        return conn

    def test_logout_session_token(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        conn.execute("INSERT INTO sessions VALUES ('tok', 'p', 'u', 'user', 0, NULL, 1, 1)")
        conn.execute("INSERT INTO locks VALUES ('p', 'labels/a.txt', 'tok', 'u', 1)")
        conn.commit()

        logout_session_token(conn=conn, db_lock=lock, token="tok")

        c1 = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        c2 = conn.execute("SELECT COUNT(*) AS c FROM locks").fetchone()
        self.assertEqual(int(c1["c"]), 0)
        self.assertEqual(int(c2["c"]), 0)

    def test_delete_user_sessions_and_locks(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        conn.execute("INSERT INTO sessions VALUES ('t1', 'p', 'u', 'user', 0, NULL, 1, 1)")
        conn.execute("INSERT INTO sessions VALUES ('t2', 'p', 'u', 'user', 0, NULL, 1, 1)")
        conn.execute("INSERT INTO sessions VALUES ('t3', 'p', 'v', 'user', 0, NULL, 1, 1)")
        conn.execute("INSERT INTO locks VALUES ('p', 'a', 't1', 'u', 1)")
        conn.execute("INSERT INTO locks VALUES ('p', 'b', 't2', 'u', 1)")
        conn.execute("INSERT INTO locks VALUES ('p', 'c', 't3', 'v', 1)")
        conn.commit()

        removed = delete_user_sessions_and_locks(conn=conn, db_lock=lock, project_id="p", username="u")

        self.assertEqual(removed, 2)
        c_sessions = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        c_locks = conn.execute("SELECT COUNT(*) AS c FROM locks").fetchone()
        self.assertEqual(int(c_sessions["c"]), 1)
        self.assertEqual(int(c_locks["c"]), 1)

    def test_owner_count_for_project(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO users VALUES ('p', 'a', 'x', 1, 'owner', 'z', 't')")
        conn.execute("INSERT INTO users VALUES ('p', 'b', 'x', 1, 'owner', 'z', 't')")
        conn.execute("INSERT INTO users VALUES ('p', 'c', 'x', 1, 'admin', 'z', 't')")
        conn.execute("INSERT INTO users VALUES ('q', 'd', 'x', 1, 'owner', 'z', 't')")
        conn.commit()

        self.assertEqual(owner_count_for_project(conn=conn, project_id="p"), 2)


if __name__ == "__main__":
    unittest.main()


