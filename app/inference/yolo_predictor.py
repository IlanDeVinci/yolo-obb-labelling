"""YOLO inference — async worker using QThread. Supports both OBB and regular detection models."""
from __future__ import annotations
from pathlib import Path
from typing import Callable
from app.inference.runtime_bootstrap import (
    get_inference_diag_log_path,
    get_yolo_class as _bootstrap_get_yolo_class,
    warmup_inference_runtime,
)


def is_inference_available() -> tuple[bool, str]:
    """Return runtime inference availability and error detail if missing."""
    return warmup_inference_runtime()


def get_yolo_class():
    """Return YOLO class or raise RuntimeError with detail."""
    return _bootstrap_get_yolo_class()


# Important on Windows: pre-load torch/ultralytics before Qt imports.
INFERENCE_AVAILABLE, INFERENCE_ERROR = warmup_inference_runtime()
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

    def _is_thread_running(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return thread.isRunning()
        except RuntimeError:
            # Underlying C++ object was already deleted.
            self._thread = None
            self._worker = None
            return False

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

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
        if self._is_thread_running():
            return  # Already running

        self._thread = QThread()
        self._worker = _InferenceWorker(model_path, image_path, conf, use_obb)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_done)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.error.connect(on_error)
        self._worker.error.connect(self._thread.quit)
        self._worker.error.connect(self._worker.deleteLater)
        # Cleanup
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def is_running(self) -> bool:
        return self._is_thread_running()
