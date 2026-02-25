"""YOLO inference — async worker using QThread. Supports both OBB and regular detection models."""
from __future__ import annotations
import ctypes
import os
import sys
from pathlib import Path
from typing import Callable

_YOLO = None
INFERENCE_ERROR = ""


def _prepare_windows_torch_dlls() -> None:
    if os.name != "nt":
        return

    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    torch_lib = venv_site / "torch" / "lib"
    if not torch_lib.exists():
        return

    torch_lib_str = str(torch_lib)
    current_path = os.environ.get("PATH", "")
    if torch_lib_str not in current_path:
        os.environ["PATH"] = torch_lib_str + os.pathsep + current_path

    try:
        os.add_dll_directory(torch_lib_str)
    except Exception:
        pass


def _preload_torch_core_dlls() -> None:
    if os.name != "nt":
        return

    venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    torch_lib = venv_site / "torch" / "lib"
    if not torch_lib.exists():
        return

    # Best-effort preload for flaky DLL init order on some Windows setups.
    for dll_name in ("c10.dll", "torch_cpu.dll", "fbgemm.dll"):
        dll_path = torch_lib / dll_name
        if not dll_path.exists():
            continue
        try:
            ctypes.WinDLL(str(dll_path))
        except Exception:
            pass


def _try_import_yolo() -> bool:
    """Try to import ultralytics.YOLO lazily.

    Returns:
        True if YOLO is available in the current Python environment.
    """
    global _YOLO, INFERENCE_ERROR

    if _YOLO is not None:
        INFERENCE_ERROR = ""
        return True

    _prepare_windows_torch_dlls()

    try:
        import torch  # noqa: F401
        from ultralytics import YOLO as yolo_class

        _YOLO = yolo_class
        INFERENCE_ERROR = ""
        return True
    except Exception as exc:
        # Retry once with conservative env knobs for common Windows DLL init issues
        if "WinError 1114" in str(exc):
            _prepare_windows_torch_dlls()
            _preload_torch_core_dlls()
            try:
                import torch  # noqa: F401
                from ultralytics import YOLO as yolo_class

                _YOLO = yolo_class
                INFERENCE_ERROR = ""
                return True
            except Exception as retry_exc:
                _YOLO = None
                INFERENCE_ERROR = str(retry_exc)
                return False

        _YOLO = None
        INFERENCE_ERROR = str(exc)
        return False


def is_inference_available() -> tuple[bool, str]:
    """Return runtime inference availability and error detail if missing."""
    available = _try_import_yolo()
    return available, INFERENCE_ERROR


def get_yolo_class():
    """Return YOLO class or raise RuntimeError with detail."""
    if not _try_import_yolo():
        raise RuntimeError(INFERENCE_ERROR or "ultralytics import failed")
    return _YOLO


# Important on Windows: pre-load torch/ultralytics before Qt imports.
INFERENCE_AVAILABLE = _try_import_yolo()
from PyQt6.QtCore import QThread, QObject, pyqtSignal

from app.models.obb_label import OBBLabel, BBoxLabel, Label


class _InferenceWorker(QObject):
    finished = pyqtSignal(list)    # list[Label]
    error = pyqtSignal(str)

    def __init__(
        self,
        model_path: str,
        image_path: Path,
        conf: float,
        use_obb: bool = True,
    ) -> None:
        super().__init__()
        self._model_path = model_path
        self._image_path = image_path
        self._conf = conf
        self._use_obb = use_obb

    def run(self) -> None:
        try:
            yolo_class = get_yolo_class()
            model = yolo_class(self._model_path)
            results = model.predict(
                source=str(self._image_path),
                conf=self._conf,
                save=False,
                verbose=False,
            )
            labels: list[Label] = []

            if not results:
                self.finished.emit(labels)
                return

            result = results[0]

            # Try OBB format first (for OBB models)
            if hasattr(result, 'obb') and result.obb is not None and len(result.obb.cls) > 0:
                obb = result.obb
                # xyxyxyxyn: normalized 4-point coords, shape (N, 4, 2)
                coords = obb.xyxyxyxyn.cpu().numpy()
                for i in range(len(obb.cls)):
                    pts = coords[i].reshape(8).tolist()
                    if self._use_obb:
                        labels.append(OBBLabel(
                            class_idx=int(obb.cls[i].item()),
                            points=pts,
                            conf=float(obb.conf[i].item()),
                        ))
                    else:
                        # Convert OBB to axis-aligned bbox
                        labels.append(BBoxLabel.from_corners(
                            class_idx=int(obb.cls[i].item()),
                            corners=pts,
                            conf=float(obb.conf[i].item()),
                        ))

            # Try standard detection format (for regular detection models)
            elif hasattr(result, 'boxes') and result.boxes is not None and len(result.boxes.cls) > 0:
                boxes = result.boxes
                # xywhn: normalized center x, y, width, height
                coords = boxes.xywhn.cpu().numpy()
                for i in range(len(boxes.cls)):
                    x_center, y_center, width, height = coords[i].tolist()
                    if self._use_obb:
                        # Convert bbox to OBB format (4 corners)
                        half_w = width / 2
                        half_h = height / 2
                        pts = [
                            x_center - half_w, y_center - half_h,  # top-left
                            x_center + half_w, y_center - half_h,  # top-right
                            x_center + half_w, y_center + half_h,  # bottom-right
                            x_center - half_w, y_center + half_h,  # bottom-left
                        ]
                        labels.append(OBBLabel(
                            class_idx=int(boxes.cls[i].item()),
                            points=pts,
                            conf=float(boxes.conf[i].item()),
                        ))
                    else:
                        labels.append(BBoxLabel(
                            class_idx=int(boxes.cls[i].item()),
                            x_center=x_center,
                            y_center=y_center,
                            width=width,
                            height=height,
                            conf=float(boxes.conf[i].item()),
                        ))

            self.finished.emit(labels)
        except Exception as exc:
            self.error.emit(str(exc))


class YoloPredictor:
    """Manages async YOLO inference (supports both OBB and regular detection models)."""

    def __init__(self) -> None:
        self._thread: QThread | None = None
        self._worker: _InferenceWorker | None = None

    def predict_async(
        self,
        model_path: str,
        image_path: Path,
        conf: float,
        on_done: Callable[[list[Label]], None],
        on_error: Callable[[str], None],
        use_obb: bool = True,
    ) -> None:
        """Run inference in a background thread; calls on_done or on_error when finished.

        Args:
            model_path: Path to the YOLO model (.pt file)
            image_path: Path to the image to run inference on
            conf: Confidence threshold
            on_done: Callback with list of labels when inference completes
            on_error: Callback with error message if inference fails
            use_obb: If True, output OBB labels; if False, output BBox labels
        """
        if self._thread and self._thread.isRunning():
            return  # Already running

        self._thread = QThread()
        self._worker = _InferenceWorker(model_path, image_path, conf, use_obb)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_done)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(on_error)
        self._worker.error.connect(self._thread.quit)
        # Cleanup
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.isRunning())
