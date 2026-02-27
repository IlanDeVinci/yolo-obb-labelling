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


from PyQt6.QtCore import QThread, QObject, pyqtSignal

from app.models.obb_label import OBBLabel, BBoxLabel, Label
from app.utils.image_io import prepare_inference_source, cleanup_inference_source


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _get_result_image_size(result) -> tuple[float, float] | None:
    """Return original image size (width, height) from a Ultralytics result."""
    orig_shape = getattr(result, "orig_shape", None)
    if orig_shape is not None and len(orig_shape) >= 2:
        h = float(orig_shape[0])
        w = float(orig_shape[1])
        if w > 0 and h > 0:
            return w, h
    return None


def labels_from_result(result, use_obb: bool) -> list[Label]:
    """Convert one Ultralytics result object to app labels.

    Uses pixel-space outputs (`xyxyxyxy` / `xyxy`) normalized by original image
    dimensions to avoid letterbox-related coordinate drift/distortion.
    """
    labels: list[Label] = []
    image_size = _get_result_image_size(result)

    if hasattr(result, "obb") and result.obb is not None and len(result.obb.cls) > 0:
        obb = result.obb
        if image_size is not None:
            img_w, img_h = image_size
            coords_px = obb.xyxyxyxy.cpu().numpy()  # shape (N, 4, 2)
            for i in range(len(obb.cls)):
                flat = coords_px[i].reshape(8).tolist()
                pts = [
                    _clamp01(float(v) / img_w) if idx % 2 == 0 else _clamp01(float(v) / img_h)
                    for idx, v in enumerate(flat)
                ]
                if use_obb:
                    labels.append(OBBLabel(
                        class_idx=int(obb.cls[i].item()),
                        points=pts,
                        conf=float(obb.conf[i].item()),
                    ))
                else:
                    labels.append(BBoxLabel.from_corners(
                        class_idx=int(obb.cls[i].item()),
                        corners=pts,
                        conf=float(obb.conf[i].item()),
                    ))
            return labels

        # Fallback if image size unavailable
        coords_n = obb.xyxyxyxyn.cpu().numpy()
        for i in range(len(obb.cls)):
            pts = [_clamp01(float(v)) for v in coords_n[i].reshape(8).tolist()]
            if use_obb:
                labels.append(OBBLabel(
                    class_idx=int(obb.cls[i].item()),
                    points=pts,
                    conf=float(obb.conf[i].item()),
                ))
            else:
                labels.append(BBoxLabel.from_corners(
                    class_idx=int(obb.cls[i].item()),
                    corners=pts,
                    conf=float(obb.conf[i].item()),
                ))
        return labels

    if hasattr(result, "boxes") and result.boxes is not None and len(result.boxes.cls) > 0:
        boxes = result.boxes
        if image_size is not None:
            img_w, img_h = image_size
            coords_xyxy = boxes.xyxy.cpu().numpy()  # shape (N, 4)
            for i in range(len(boxes.cls)):
                x1, y1, x2, y2 = [float(v) for v in coords_xyxy[i].tolist()]
                x1n = _clamp01(x1 / img_w)
                y1n = _clamp01(y1 / img_h)
                x2n = _clamp01(x2 / img_w)
                y2n = _clamp01(y2 / img_h)
                x_min, x_max = min(x1n, x2n), max(x1n, x2n)
                y_min, y_max = min(y1n, y2n), max(y1n, y2n)

                if use_obb:
                    pts = [
                        x_min, y_min,
                        x_max, y_min,
                        x_max, y_max,
                        x_min, y_max,
                    ]
                    labels.append(OBBLabel(
                        class_idx=int(boxes.cls[i].item()),
                        points=pts,
                        conf=float(boxes.conf[i].item()),
                    ))
                else:
                    labels.append(BBoxLabel(
                        class_idx=int(boxes.cls[i].item()),
                        x_center=(x_min + x_max) / 2,
                        y_center=(y_min + y_max) / 2,
                        width=max(0.0, x_max - x_min),
                        height=max(0.0, y_max - y_min),
                        conf=float(boxes.conf[i].item()),
                    ))
            return labels

        # Fallback if image size unavailable
        coords = boxes.xywhn.cpu().numpy()
        for i in range(len(boxes.cls)):
            x_center, y_center, width, height = [float(v) for v in coords[i].tolist()]
            x_center = _clamp01(x_center)
            y_center = _clamp01(y_center)
            width = max(0.0, _clamp01(width))
            height = max(0.0, _clamp01(height))
            if use_obb:
                half_w = width / 2
                half_h = height / 2
                pts = [
                    _clamp01(x_center - half_w), _clamp01(y_center - half_h),
                    _clamp01(x_center + half_w), _clamp01(y_center - half_h),
                    _clamp01(x_center + half_w), _clamp01(y_center + half_h),
                    _clamp01(x_center - half_w), _clamp01(y_center + half_h),
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

    return labels


class _InferenceWorker(QObject):
    finished = pyqtSignal(list)    # list[Label]
    error = pyqtSignal(str)

    def __init__(
        self,
        model_path: str,
        image_path: Path,
        conf: float,
        class_filter: list[int] | None = None,
        use_obb: bool = True,
    ) -> None:
        super().__init__()
        self._model_path = model_path
        self._image_path = image_path
        self._conf = conf
        self._class_filter = class_filter
        self._use_obb = use_obb

    def run(self) -> None:
        temp_path: str | None = None
        try:
            yolo_class = get_yolo_class()
            model = yolo_class(self._model_path)
            source, temp_path = prepare_inference_source(self._image_path)
            results = model.predict(
                source=source,
                conf=self._conf,
                classes=self._class_filter,
                save=False,
                verbose=False,
            )
            labels: list[Label] = []

            if not results:
                self.finished.emit(labels)
                return

            result = results[0]

            labels = labels_from_result(result, use_obb=self._use_obb)

            self.finished.emit(labels)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            cleanup_inference_source(temp_path)


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
        class_filter: list[int] | None,
        on_done: Callable[[list[Label]], None],
        on_error: Callable[[str], None],
        use_obb: bool = True,
    ) -> None:
        """Run inference in a background thread; calls on_done or on_error when finished.

        Args:
            model_path: Path to the YOLO model (.pt file)
            image_path: Path to the image to run inference on
            conf: Confidence threshold
            class_filter: Optional class-id filter passed to Ultralytics
            on_done: Callback with list of labels when inference completes
            on_error: Callback with error message if inference fails
            use_obb: If True, output OBB labels; if False, output BBox labels
        """
        if self._is_thread_running():
            return  # Already running

        self._thread = QThread()
        self._worker = _InferenceWorker(model_path, image_path, conf, class_filter, use_obb)
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
