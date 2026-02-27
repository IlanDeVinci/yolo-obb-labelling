"""Robust image I/O helpers for display and inference.

Ensures a consistent pixel/orientation pipeline across Qt canvas rendering and
model inference, including EXIF-aware JPEG handling and WebP support via PIL.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, ImageFile
from PyQt6.QtGui import QImage, QPixmap, QImageReader

# Some phone-exported JPGs can be slightly truncated but still visually valid.
ImageFile.LOAD_TRUNCATED_IMAGES = True

_HEIF_PLUGIN_OK = False
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    _HEIF_PLUGIN_OK = True
except Exception:
    _HEIF_PLUGIN_OK = False


def detect_container_format(image_path: Path) -> str | None:
    """Sniff container format from file signature when possible."""
    try:
        with image_path.open("rb") as fh:
            head = fh.read(64)
    except Exception:
        return None

    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12].lower()
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "heic"
        if brand in {b"avif", b"avis"}:
            return "avif"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "webp"
    return None


def decode_failure_hint(image_path: Path) -> str:
    fmt = detect_container_format(image_path)
    suffix = image_path.suffix.lower()
    if fmt == "heic" and suffix in {".jpg", ".jpeg"}:
        return (
            f"{image_path.name} is HEIC content saved with a JPG extension. "
            "Rename to .heic or install pillow-heif support."
        )
    if fmt == "heic" and not _HEIF_PLUGIN_OK:
        return (
            f"{image_path.name} appears to be HEIC/HEIF. "
            "Install pillow-heif to open it in-app."
        )
    if fmt == "avif":
        return f"{image_path.name} appears to be AVIF and may not be supported by this runtime."
    return f"Unable to display image: {image_path.name}"


def _read_with_qt(image_path: Path) -> QPixmap:
    """Try Qt decoder first, with EXIF auto-transform and content sniffing."""
    reader = QImageReader(str(image_path))
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    img = reader.read()
    if not img.isNull():
        pix = QPixmap.fromImage(img)
        if not pix.isNull():
            return pix
    return QPixmap()


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
    # 1) Qt reader path (fast, handles many formats with EXIF auto-transform)
    pix = _read_with_qt(image_path)
    if not pix.isNull():
        return pix

    # 2) Pillow path (more tolerant for unusual/partially truncated JPEGs)
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

    # 3) OpenCV fallback (optional; robust JPEG decoder in many environments)
    try:
        import cv2  # type: ignore
        import numpy as np

        arr = np.fromfile(str(image_path), dtype=np.uint8)
        decoded = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        if decoded is not None:
            if len(decoded.shape) == 2:
                h, w = decoded.shape
                qimg = QImage(decoded.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
            elif decoded.shape[2] == 4:
                rgba = cv2.cvtColor(decoded, cv2.COLOR_BGRA2RGBA)
                h, w = rgba.shape[:2]
                qimg = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            else:
                rgb = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
                h, w = rgb.shape[:2]
                qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
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
