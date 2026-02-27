"""Windows-focused runtime bootstrap for torch/ultralytics imports."""
from __future__ import annotations

import ctypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_YOLO_CLASS = None
_LAST_ERROR = ""

# Keep runtime deterministic in app mode: don't let ultralytics try pip installs.
os.environ.setdefault("YOLO_AUTOINSTALL", "False")
os.environ.setdefault("ULTRALYTICS_AUTOINSTALL", "0")


def get_inference_diag_log_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "diagnostics" / "inference_dll.log"


def _diag_log(message: str) -> None:
    try:
        log_path = get_inference_diag_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] pid={os.getpid()} {message}\n")
    except Exception:
        pass


def prepare_windows_ml_runtime() -> None:
    if os.name != "nt":
        return

    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    torch_lib = venv_site / "torch" / "lib"
    _diag_log(f"prepare_dlls exe={sys.executable} torch_lib={torch_lib}")

    if not torch_lib.exists():
        _diag_log("prepare_dlls torch_lib_missing")
        return

    torch_lib_str = str(torch_lib)
    current_path = os.environ.get("PATH", "")
    if torch_lib_str not in current_path:
        os.environ["PATH"] = torch_lib_str + os.pathsep + current_path

    try:
        os.add_dll_directory(torch_lib_str)
        _diag_log("prepare_dlls add_dll_directory_ok")
    except Exception:
        _diag_log("prepare_dlls add_dll_directory_failed")


def _preload_torch_core_dlls() -> None:
    if os.name != "nt":
        return

    venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    torch_lib = venv_site / "torch" / "lib"
    if not torch_lib.exists():
        _diag_log("preload_dlls torch_lib_missing")
        return

    for dll_name in ("c10.dll", "torch_cpu.dll", "fbgemm.dll"):
        dll_path = torch_lib / dll_name
        if not dll_path.exists():
            continue
        try:
            ctypes.WinDLL(str(dll_path))
            _diag_log(f"preload_dlls ok {dll_name}")
        except Exception:
            _diag_log(f"preload_dlls failed {dll_name}")


def _attempt_import() -> tuple[bool, Any]:
    try:
        import torch  # noqa: F401
        import torchvision  # noqa: F401
        from ultralytics import YOLO as yolo_class

        _diag_log("attempt_import success")
        return True, yolo_class
    except Exception as import_exc:  # noqa: BLE001
        _diag_log(f"attempt_import failed {type(import_exc).__name__}: {import_exc}")
        return False, import_exc


def warmup_inference_runtime() -> tuple[bool, str]:
    global _YOLO_CLASS, _LAST_ERROR

    if _YOLO_CLASS is not None:
        _LAST_ERROR = ""
        return True, ""

    _diag_log("import_yolo start")

    ok, payload = _attempt_import()
    if ok:
        _YOLO_CLASS = payload
        _LAST_ERROR = ""
        _diag_log("import_yolo stage1_success")
        return True, ""

    first_exc = payload
    err_txt = str(first_exc)
    if os.name != "nt" or "WinError 1114" not in err_txt:
        _YOLO_CLASS = None
        _LAST_ERROR = err_txt
        _diag_log(f"import_yolo non_win1114_fail {err_txt}")
        return False, _LAST_ERROR

    time.sleep(0.5)
    _diag_log("import_yolo sleep_0.50_done")
    prepare_windows_ml_runtime()
    _preload_torch_core_dlls()
    time.sleep(0.75)
    _diag_log("import_yolo post_prepare_sleep_0.75_done")

    last_exc: Exception | None = first_exc
    for delay in (0.0, 0.5, 1.0, 1.5):
        if delay:
            time.sleep(delay)
            _diag_log(f"import_yolo retry_sleep_{delay}")
        prepare_windows_ml_runtime()
        _preload_torch_core_dlls()
        ok, payload = _attempt_import()
        if ok:
            _YOLO_CLASS = payload
            _LAST_ERROR = ""
            _diag_log(f"import_yolo retry_success delay={delay}")
            return True, ""
        last_exc = payload
        _diag_log(f"import_yolo retry_failed delay={delay}")

    _YOLO_CLASS = None
    _LAST_ERROR = str(last_exc) if last_exc is not None else err_txt
    _diag_log(f"import_yolo final_fail {_LAST_ERROR}")
    return False, _LAST_ERROR


def get_yolo_class():
    ok, err = warmup_inference_runtime()
    if not ok:
        raise RuntimeError(err or "ultralytics import failed")
    return _YOLO_CLASS


def get_inference_error() -> str:
    return _LAST_ERROR
