from __future__ import annotations

import sqlite3
import threading
import unittest

from sync_backend.sync_core import (
    conflict_detail,
    find_lock_conflict,
    get_other_lock_holder,
    holds_explicit_lock,
    list_project_locks,
    release_session_locks,
    upsert_active_lock,
)


class TestSyncLockStore(unittest.TestCase):
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
            """
        )
        return conn

    def test_release_session_locks(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        conn.execute("INSERT INTO sessions VALUES ('tok', 'p', 'u', 'user', 0, 'x.txt', 1, 1)")
        conn.execute("INSERT INTO locks VALUES ('p', 'x.txt', 'tok', 'u', 1)")
        conn.commit()

        release_session_locks(conn=conn, db_lock=lock, token="tok")

        c = conn.execute("SELECT COUNT(*) AS c FROM locks").fetchone()
        self.assertEqual(int(c["c"]), 0)
        row = conn.execute("SELECT active_file FROM sessions WHERE token = 'tok'").fetchone()
        self.assertIsNone(row["active_file"])

    def test_upsert_and_list_locks(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        conn.execute("INSERT INTO sessions VALUES ('tok', 'p', 'u', 'user', 0, NULL, 1, 1)")
        conn.commit()

        upsert_active_lock(
            conn=conn,
            db_lock=lock,
            project_id="p",
            path="labels/a.txt",
            token="tok",
            username="u",
            now_ms=100,
        )

        by_path = list_project_locks(conn=conn, project_id="p")
        self.assertEqual(by_path[0]["path"], "labels/a.txt")

        row = conn.execute("SELECT active_file, last_seen FROM sessions WHERE token = 'tok'").fetchone()
        self.assertEqual(row["active_file"], "labels/a.txt")
        self.assertEqual(int(row["last_seen"]), 100)

    def test_conflict_and_holder_helpers(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO locks VALUES ('p', 'labels/a.txt', 'tok2', 'other', 55)")
        conn.commit()

        conflict = find_lock_conflict(conn=conn, project_id="p", path="labels/a.txt", token="tok1")
        assert conflict is not None
        detail = conflict_detail(conflict_row=conflict, path="labels/a.txt")
        self.assertEqual(detail["lockedBy"], "other")

        self.assertFalse(holds_explicit_lock(conn=conn, project_id="p", path="labels/a.txt", token="tok1"))
        self.assertTrue(holds_explicit_lock(conn=conn, project_id="p", path="labels/a.txt", token="tok2"))
        self.assertEqual(get_other_lock_holder(conn=conn, project_id="p", path="labels/a.txt", token="tok1"), "other")
        self.assertIsNone(get_other_lock_holder(conn=conn, project_id="p", path="labels/a.txt", token="tok2"))


if __name__ == "__main__":
    unittest.main()


