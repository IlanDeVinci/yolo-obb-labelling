from __future__ import annotations

import unittest

from sync_backend.sync_core import (
    clamp_admin_images_limit,
    clamp_admin_table_limit,
    clamp_recent_changes_limit,
    clamp_sync_changes_limit,
)


class TestSyncLimits(unittest.TestCase):
    def test_clamp_sync_changes_limit(self) -> None:
        self.assertEqual(clamp_sync_changes_limit(1), 50)
        self.assertEqual(clamp_sync_changes_limit(1200), 1200)
        self.assertEqual(clamp_sync_changes_limit(99999), 2500)

    def test_other_limits(self) -> None:
        self.assertEqual(clamp_admin_table_limit(0), 1)
        self.assertEqual(clamp_admin_table_limit(999), 500)
        self.assertEqual(clamp_recent_changes_limit(1), 5)
        self.assertEqual(clamp_recent_changes_limit(999), 120)
        self.assertEqual(clamp_admin_images_limit(0), 1)
        self.assertEqual(clamp_admin_images_limit(999999), 20000)


if __name__ == "__main__":
    unittest.main()


