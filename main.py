"""Entry point for the YOLO OBB Labeller."""
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import qInstallMessageHandler

from app.inference.runtime_bootstrap import (
    prepare_windows_ml_runtime,
    warmup_inference_runtime,
)


_QT_PREV_HANDLER = None
_QT_PAINTER_NOISE_PREFIXES = (
    "QPainter::end: Painter not active, aborted",
    "QPainter::begin: Paint device returned engine == 0, type: 3",
    "QPainter::setCompositionMode: Painter not active",
    "QPainter::fillRect: Painter not active",
)


def _apply_dark_palette(app) -> None:
    from PyQt6.QtGui import QPalette, QColor

    app.setStyle("Fusion")
    palette = QPalette()
    dark = QColor(45, 45, 45)
    mid_dark = QColor(60, 60, 60)
    light_text = QColor(220, 220, 220)
    highlight = QColor(42, 130, 218)

    palette.setColor(QPalette.ColorRole.Window, dark)
    palette.setColor(QPalette.ColorRole.WindowText, light_text)
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.AlternateBase, mid_dark)
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, light_text)
    palette.setColor(QPalette.ColorRole.Button, mid_dark)
    palette.setColor(QPalette.ColorRole.ButtonText, light_text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)


def _prepare_windows_ml_runtime() -> None:
    """Best-effort Windows runtime prep for torch/ultralytics DLL loading."""
    prepare_windows_ml_runtime()


def _install_qt_log_filter() -> None:
    """Suppress known noisy Qt painter warnings while keeping other Qt logs."""
    if os.environ.get("YOLO_LABELLER_SHOW_QT_PAINTER_LOGS", "").strip() == "1":
        return

    def _handler(msg_type, context, message):
        text = str(message or "")
        if text.startswith(_QT_PAINTER_NOISE_PREFIXES):
            return
        if _QT_PREV_HANDLER is not None:
            _QT_PREV_HANDLER(msg_type, context, message)
        else:
            print(text, file=sys.stderr)

    global _QT_PREV_HANDLER
    _QT_PREV_HANDLER = qInstallMessageHandler(_handler)


def main() -> None:
    _prepare_windows_ml_runtime()

    # Warmup inference runtime before loading Qt to reduce WinError 1114 races.
    warmup_inference_runtime()
    if os.name == "nt":
        time.sleep(0.5)
        _prepare_windows_ml_runtime()

    from PyQt6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    _install_qt_log_filter()

    app = QApplication(sys.argv)
    app.setApplicationName("YOLO OBB Labeller")
    app.setOrganizationName("YoloOBBLabeller")
    _apply_dark_palette(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
