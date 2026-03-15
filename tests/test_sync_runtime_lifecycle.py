from __future__ import annotations

import threading
import unittest

from sync_backend.sync_core import run_shutdown, run_startup


class TestSyncRuntimeLifecycle(unittest.TestCase):
    def test_run_startup_and_shutdown(self) -> None:
        touched: list[str] = []
        worker_started = threading.Event()

        def init_db() -> None:
            touched.append("init")

        def backup() -> None:
            touched.append("backup")

        def worker() -> None:
            worker_started.set()

        thread = run_startup(init_db=init_db, ensure_daily_backup=backup, backup_worker=worker)
        self.assertTrue(thread.daemon)
        self.assertEqual(touched, ["init", "backup"])
        worker_started.wait(timeout=1.0)
        self.assertTrue(worker_started.is_set())

        stop_event = threading.Event()
        run_shutdown(stop_event=stop_event)
        self.assertTrue(stop_event.is_set())


if __name__ == "__main__":
    unittest.main()


