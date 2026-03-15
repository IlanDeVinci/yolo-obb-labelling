from __future__ import annotations

from pathlib import Path
from typing import Callable

_SORT_LABELS = {
    "name_asc": "Name A-Z",
    "name_desc": "Name Z-A",
    "size_asc": "Size Small-Large",
    "size_desc": "Size Large-Small",
    "mtime_desc": "Newest First",
    "mtime_asc": "Oldest First",
}


def image_sort_label(mode: str | None) -> str:
    selected = str(mode or "name_asc")
    return _SORT_LABELS.get(selected, "Name A-Z")


def sorted_image_paths(
    images: list[Path],
    *,
    mode: str | None,
    project_relative_path_for_sync: Callable[[Path], str],
    cloud_meta: dict[str, tuple[int, int]] | None = None,
) -> list[Path]:
    selected_mode = str(mode or "name_asc")
    lowered_name = lambda p: p.name.lower()  # noqa: E731

    if selected_mode in {"name_asc", "name_desc"}:
        return sorted(images, key=lowered_name, reverse=(selected_mode == "name_desc"))

    meta = cloud_meta or {}

    def size_for(path: Path) -> int:
        rel = project_relative_path_for_sync(path)
        if rel and rel in meta:
            return int(meta[rel][0])
        try:
            return int(path.stat().st_size)
        except OSError:
            return -1

    def mtime_for(path: Path) -> int:
        rel = project_relative_path_for_sync(path)
        if rel and rel in meta:
            return int(meta[rel][1])
        try:
            return int(path.stat().st_mtime * 1000)
        except OSError:
            return 0

    if selected_mode in {"size_asc", "size_desc"}:
        return sorted(
            images,
            key=lambda p: (size_for(p), lowered_name(p)),
            reverse=(selected_mode == "size_desc"),
        )

    if selected_mode in {"mtime_asc", "mtime_desc"}:
        return sorted(
            images,
            key=lambda p: (mtime_for(p), lowered_name(p)),
            reverse=(selected_mode == "mtime_desc"),
        )

    return sorted(images, key=lowered_name)
