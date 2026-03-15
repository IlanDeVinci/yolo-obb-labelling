from __future__ import annotations

import unittest

from app.ui.sync_status_merge import merge_sync_status_with_provider


class _Provider:
    def cache_stats(self):
        return {"cached": 1}

    def telemetry(self):
        return {"hits": 2}


class TestSyncStatusMerge(unittest.TestCase):
    def test_merge_sync_status_with_provider(self) -> None:
        merged = merge_sync_status_with_provider({"connected": True}, _Provider())
        self.assertTrue(merged["connected"])
        self.assertEqual(merged["imageCache"]["cached"], 1)
        self.assertEqual(merged["imageTelemetry"]["hits"], 2)


if __name__ == "__main__":
    unittest.main()


