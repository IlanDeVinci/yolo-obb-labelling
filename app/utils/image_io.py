"""Robust image I/O helpers for display and inference.

Ensures a consistent pixel/orientation pipeline across Qt canvas rendering and
model inference, including EXIF-aware JPEG handling and WebP support via PIL.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from PyQt6.QtGui import QImage, QPixmap


def _load_oriented_pil(image_path: Path) -> Image.Image:
    """Load image with EXIF orientation applied and convert to RGBA/RGB."""
    with Image.open(image_path) as src:
        img = ImageOps.exif_transpose(src)
        if img.mode in ("RGBA", "LA"):
            return img.convert("RGBA")
        if img.mode in ("RGB",):
            return img.copy()
        return img.convert("RGB")


def load_qpixmap(image_path: Path) -> QPixmap:
    """Load an image into QPixmap robustly across JPG/PNG/WebP and EXIF cases."""
    try:
        img = _load_oriented_pil(image_path)
        if img.mode == "RGBA":
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(
                data,
                img.width,
                img.height,
                img.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
        else:
            rgb = img.convert("RGB")
            data = rgb.tobytes("raw", "RGB")
            qimg = QImage(
                data,
                rgb.width,
                rgb.height,
                rgb.width * 3,
                QImage.Format.Format_RGB888,
            ).copy()
        pix = QPixmap.fromImage(qimg)
        if not pix.isNull():
            return pix
    except Exception:
        pass

    return QPixmap(str(image_path))


def prepare_inference_source(image_path: Path) -> tuple[str, str | None]:
    """Prepare a normalized file source for model.predict.

    Returns `(source, temp_path)` where temp_path is removed by
    `cleanup_inference_source` when not None.
    """
    try:
        img = _load_oriented_pil(image_path).convert("RGB")
        fd, tmp_path = tempfile.mkstemp(prefix="yolo_labeller_", suffix=".png")
        os.close(fd)
        img.save(tmp_path, format="PNG")
        return tmp_path, tmp_path
    except Exception:
        return str(image_path), None


def cleanup_inference_source(temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        Path(temp_path).unlink(missing_ok=True)
    except Exception:
        pass
