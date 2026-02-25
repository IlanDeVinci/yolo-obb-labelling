@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [1/4] Verification de Python...
set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo Python introuvable. Installe Python 3 puis relance ce script.
    exit /b 1
)

echo [2/4] Creation du venv (.venv) si necessaire...
if not exist ".venv\Scripts\python.exe" (
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo Echec creation du venv.
        exit /b 1
    )
)

echo [3/4] Installation des dependances...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo Echec mise a jour pip.
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Echec installation requirements.txt
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements-inference.txt
if errorlevel 1 (
    echo Echec installation requirements-inference.txt
    exit /b 1
)

echo [4/4] Verification inference (ultralytics + torch)...
".venv\Scripts\python.exe" -c "import ultralytics, torch; print('ultralytics', ultralytics.__version__); print('torch', torch.__version__)"
if errorlevel 1 (
    echo Attention: inference non validee. Le mode annotation reste utilisable.
    exit /b 1
)

echo Setup termine avec succes.
exit /b 0
