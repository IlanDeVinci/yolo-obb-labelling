from __future__ import annotations


def build_missing_inference_message(*, inference_error: str, sys_executable: str, diag_log_path: str) -> tuple[str, str]:
    details = f"\n\nDetail: {inference_error}" if inference_error else ""
    runtime = f"\n\nInterpreteur actuel:\n    {sys_executable}"
    diag_log = f"\n\nLog diagnostic:\n    {diag_log_path}"
    win1114_help = ""
    if "WinError 1114" in inference_error:
        win1114_help = (
            "\n\nCorrection WinError 1114 (DLL):\n"
            "1) Redemarrez VS Code puis relancez l'application avec l'interpreteur du projet (.venv).\n"
            "2) Si besoin, reinstallez torch CPU dans cet environnement:\n"
            "    python -m pip install --upgrade --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu\n"
            "3) Installez/reparez Microsoft Visual C++ Redistributable 2015-2022 (x64), puis redemarrez Windows."
        )
    text = (
        "Le module d'inference 'ultralytics' n'est pas disponible dans l'environnement Python actuel.\n\n"
        "Installez-le dans CE MEME environnement avec:\n"
        "    python -m pip install -r requirements-inference.txt"
        f"{runtime}{diag_log}{details}{win1114_help}"
    )
    return "ultralytics indisponible", text
