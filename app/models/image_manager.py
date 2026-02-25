"""Image list navigation."""
from __future__ import annotations
from pathlib import Path


class ImageManager:
    def __init__(self) -> None:
        self._images: list[Path] = []
        self._index: int = -1
        self._split: str = "train"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_split(self, images: list[Path], split: str = "train") -> None:
        self._images = list(images)
        self._split = split
        self._index = 0 if self._images else -1

    def load_folder(self, images: list[Path]) -> None:
        self.load_split(images, split="")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @property
    def current_image(self) -> Path | None:
        if 0 <= self._index < len(self._images):
            return self._images[self._index]
        return None

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def total(self) -> int:
        return len(self._images)

    @property
    def split(self) -> str:
        return self._split

    @property
    def images(self) -> list[Path]:
        return self._images

    def has_next(self) -> bool:
        return self._index < len(self._images) - 1

    def has_prev(self) -> bool:
        return self._index > 0

    def next(self) -> Path | None:
        if self.has_next():
            self._index += 1
        return self.current_image

    def prev(self) -> Path | None:
        if self.has_prev():
            self._index -= 1
        return self.current_image

    def go_to(self, index: int) -> Path | None:
        if 0 <= index < len(self._images):
            self._index = index
        return self.current_image

    def go_to_path(self, path: Path) -> Path | None:
        try:
            self._index = self._images.index(path)
            return self.current_image
        except ValueError:
            return None
