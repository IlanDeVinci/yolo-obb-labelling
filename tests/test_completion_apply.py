from __future__ import annotations

import unittest
from pathlib import Path

from app.ui.completion_apply import apply_completion_status_to_images


class TestCompletionApply(unittest.TestCase):
    def test_apply_completion_status_to_images(self) -> None:
        names: list[str] = []
        persisted: list[str] = []
        pushed: list[str] = []

        def set_project(name: str, status: str) -> None:
            names.append(f"{name}:{status}")

        def persist(path: Path, status: str) -> None:
            persisted.append(f"{path.name}:{status}")

        def push(name: str, status: str) -> None:
            pushed.append(f"{name}:{status}")

        apply_completion_status_to_images(
            [Path("a.jpg"), Path("b.jpg")],
            status="completed",
            set_project_completion=set_project,
            persist_local_completion=persist,
            push_cloud_completion=push,
        )

        self.assertEqual(names, ["a.jpg:completed", "b.jpg:completed"])
        self.assertEqual(persisted, ["a.jpg:completed", "b.jpg:completed"])
        self.assertEqual(pushed, ["a.jpg:completed", "b.jpg:completed"])

    def test_push_errors_are_ignored(self) -> None:
        touched: list[str] = []

        def set_project(name: str, status: str) -> None:
            touched.append(name)

        def push(_name: str, _status: str) -> None:
            raise RuntimeError("boom")

        apply_completion_status_to_images(
            [Path("a.jpg")],
            status="in_progress",
            set_project_completion=set_project,
            persist_local_completion=None,
            push_cloud_completion=push,
        )

        self.assertEqual(touched, ["a.jpg"])


if __name__ == "__main__":
    unittest.main()
