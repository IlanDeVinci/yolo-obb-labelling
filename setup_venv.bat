@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "INSTALL_INFERENCE=0"
set "OFFLINE_MODE=0"

if /I "%YOLO_INSTALL_INFERENCE%"=="1" set "INSTALL_INFERENCE=1"
if /I "%YOLO_SETUP_OFFLINE%"=="1" set "OFFLINE_MODE=1"

:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="--with-inference" set "INSTALL_INFERENCE=1"
if /I "%~1"=="--offline" set "OFFLINE_MODE=1"
shift
goto :parse_args

:args_done

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

set "PIP_RETRY_COUNT=5"
set "PIP_RETRY_WAIT_SECONDS=10"

echo [3/4] Installation des dependances...
if "%OFFLINE_MODE%"=="1" (
    echo Mode offline active: aucune installation reseau ne sera tentee.
    echo Verification des dependances locales...
    ".venv\Scripts\python.exe" -c "import PyQt6, PIL, numpy, yaml"
    if errorlevel 1 (
        echo Dependances manquantes localement. Relance sans --offline avec internet.
        exit /b 1
    )
    echo Dependances de base detectees localement.
) else (
    call :pip_with_retry install --upgrade pip
    if errorlevel 1 (
        echo Attention: echec mise a jour pip. On continue avec la version actuelle.
    )

    call :pip_with_retry install -r requirements.txt
    if errorlevel 1 (
        echo Echec installation requirements.txt apres plusieurs tentatives.
        echo Verification des dependances deja presentes localement...
        ".venv\Scripts\python.exe" -c "import PyQt6, PIL, numpy, yaml"
        if errorlevel 1 (
            echo Dependances manquantes: impossible de continuer sans connexion stable.
            exit /b 1
        )
        echo Dependances detectees localement. Setup continue malgre l'echec reseau.
    )
)

if "%INSTALL_INFERENCE%"=="1" (
    if "%OFFLINE_MODE%"=="1" (
        echo [4/4] Inference demandee en mode offline ^(sans installation reseau^)...
        echo Verification inference ^(ultralytics + torch^)...
        ".venv\Scripts\python.exe" -c "import ultralytics, torch; print('ultralytics', ultralytics.__version__); print('torch', torch.__version__)"
        if errorlevel 1 (
            echo Attention: inference non disponible localement en mode offline.
            echo Relance sans --offline pour installer requirements-inference.txt
            echo Le mode annotation reste utilisable.
            exit /b 0
        )
    ) else (
        echo [4/4] Installation inference optionnelle ^(ultralytics + torch^)...
        call :pip_with_retry install -r requirements-inference.txt
        if errorlevel 1 (
            echo Attention: echec installation requirements-inference.txt
            echo Le mode annotation reste utilisable.
            exit /b 0
        )

        echo Verification inference ^(ultralytics + torch^)...
        ".venv\Scripts\python.exe" -c "import ultralytics, torch; print('ultralytics', ultralytics.__version__); print('torch', torch.__version__)"
        if errorlevel 1 (
            echo Attention: inference non validee. Le mode annotation reste utilisable.
            exit /b 0
        )
    )
) else (
    echo [4/4] Inference non installee ^(optionnelle^).
    echo Pour l'activer: setup_venv.bat --with-inference
)

echo Setup termine avec succes.
exit /b 0

:pip_with_retry
set "_PIP_ARGS=%*"
set /a _PIP_TRY=1
:pip_with_retry_loop
echo Tentative !_PIP_TRY!/!PIP_RETRY_COUNT!: pip !_PIP_ARGS!
".venv\Scripts\python.exe" -m pip --timeout 60 --retries 10 !_PIP_ARGS!
if not errorlevel 1 exit /b 0

if !_PIP_TRY! GEQ !PIP_RETRY_COUNT! exit /b 1

echo Echec reseau/timeout. Nouvelle tentative dans !PIP_RETRY_WAIT_SECONDS!s...
timeout /t !PIP_RETRY_WAIT_SECONDS! /nobreak >nul
set /a _PIP_TRY+=1
goto :pip_with_retry_loop
