from __future__ import annotations

import sqlite3
import unittest

from sync_backend.sync_core import fetch_changes_since, map_change_rows


class TestSyncChangeFeed(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
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
            """
        )
        conn.commit()
        return conn

    def test_fetch_changes_since(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES ('p', 'u', 't', 'images/a.jpg', 0, 1, 's', 'x', 1)")
        conn.execute("INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES ('p', 'u', 't', 'labels/a.txt', 0, 2, 's2', 'y', 2)")
        conn.commit()

        rows = fetch_changes_since(conn=conn, project_id="p", since=0, limit=10)
        self.assertEqual(len(rows), 2)

    def test_map_change_rows_cloud_only_image(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES ('p', 'u', 't', 'images/a.jpg', 0, 1, 's', 'payload', 1)")
        conn.commit()
        rows = fetch_changes_since(conn=conn, project_id="p", since=0, limit=10)

        out = map_change_rows(
            rows=rows,
            image_access_mode="cloud_only",
            project_id="p",
            project_uses_s3_images=True,
            is_image_path=lambda p: p.endswith(".jpg"),
            s3_get_image_base64=lambda _project_id, _path: "from-s3",
        )
        self.assertEqual(out[0]["contentBase64"], "")

    def test_map_change_rows_s3_override(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES ('p', 'u', 't', 'images/a.jpg', 0, 1, 's', 'payload', 1)")
        conn.commit()
        rows = fetch_changes_since(conn=conn, project_id="p", since=0, limit=10)

        out = map_change_rows(
            rows=rows,
            image_access_mode="hybrid",
            project_id="p",
            project_uses_s3_images=True,
            is_image_path=lambda p: p.endswith(".jpg"),
            s3_get_image_base64=lambda _project_id, _path: "from-s3",
        )
        self.assertEqual(out[0]["contentBase64"], "from-s3")


if __name__ == "__main__":
    unittest.main()


