@echo off
setlocal
cd /d "%~dp0"

call "%~dp0setup_venv.bat"
if errorlevel 1 (
    echo.
    echo Le setup a echoue. Corrige les erreurs puis relance.
    pause
    exit /b 1
)

echo Lancement de l'application...
".venv\Scripts\python.exe" main.py
exit /b %errorlevel%
