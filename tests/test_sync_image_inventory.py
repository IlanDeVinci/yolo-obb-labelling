from __future__ import annotations

import base64
import sqlite3
import unittest

from sync_backend.sync_core import (
    collect_project_image_rows_from_db,
    collect_project_image_rows_from_s3_manifest,
    fetch_project_image_status_map,
)


class TestSyncImageInventory(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE image_status (
              project_id TEXT NOT NULL,
              image_name TEXT NOT NULL,
              status TEXT NOT NULL
            );
            CREATE TABLE files (
              project_id TEXT NOT NULL,
              path TEXT NOT NULL,
              deleted INTEGER NOT NULL,
              mtime_ms INTEGER NOT NULL,
              sha1 TEXT NOT NULL,
              content_base64 TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        conn.commit()
        return conn

    def test_fetch_project_image_status_map_filters_values(self) -> None:
        conn = self._make_conn()
        conn.execute("INSERT INTO image_status (project_id, image_name, status) VALUES ('p', 'a.jpg', 'completed')")
        conn.execute("INSERT INTO image_status (project_id, image_name, status) VALUES ('p', 'b.jpg', 'invalid')")
        conn.execute("INSERT INTO image_status (project_id, image_name, status) VALUES ('x', 'x.jpg', 'completed')")
        conn.commit()

        out = fetch_project_image_status_map(conn=conn, project_id="p")
        self.assertEqual(out, {"a.jpg": "completed"})

    def test_collect_project_image_rows_from_db(self) -> None:
        conn = self._make_conn()
        raw = base64.b64encode(b"abc").decode("ascii")
        conn.execute(
            "INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at) VALUES ('p', 'images/a.jpg', 0, 10, 's', ?, 20)",
            (raw,),
        )
        conn.execute(
            "INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at) VALUES ('p', 'labels/a.txt', 0, 10, 's', '', 20)"
        )
        conn.commit()

        out = collect_project_image_rows_from_db(conn=conn, project_id="p")
        self.assertIn("images/a.jpg", out)
        self.assertEqual(out["images/a.jpg"]["sha1"], "s")
        self.assertGreaterEqual(int(out["images/a.jpg"]["sizeBytes"]), 3)

    def test_collect_project_image_rows_from_s3_manifest(self) -> None:
        manifest = [
            {"path": "images/a.jpg", "size": 123, "lastModified": 456, "etag": "e1"},
            {"path": "", "size": 0},
        ]
        out = collect_project_image_rows_from_s3_manifest(manifest)
        self.assertEqual(
            out,
            {
                "images/a.jpg": {
                    "path": "images/a.jpg",
                    "sizeBytes": 123,
                    "mtimeMs": 456,
                    "etag": "e1",
                }
            },
        )


if __name__ == "__main__":
    unittest.main()


