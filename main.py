"""Entry point for the YOLO OBB Labeller."""
import os
import sys
from pathlib import Path


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
    if os.name != "nt":
        return

    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    torch_lib = venv_site / "torch" / "lib"
    if torch_lib.exists():
        path = os.environ.get("PATH", "")
        torch_lib_str = str(torch_lib)
        if torch_lib_str not in path:
            os.environ["PATH"] = torch_lib_str + os.pathsep + path
        try:
            os.add_dll_directory(torch_lib_str)
        except Exception:
            pass


def main() -> None:
    _prepare_windows_ml_runtime()

    from PyQt6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("YOLO OBB Labeller")
    app.setOrganizationName("YoloOBBLabeller")
    _apply_dark_palette(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
