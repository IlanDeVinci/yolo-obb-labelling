@echo off
setlocal
cd /d "%~dp0"

call "%~dp0setup_venv.bat" --with-inference
if errorlevel 1 (
    echo.
    echo Installation inference echouee.
    pause
    exit /b 1
)

echo Inference installee ^(si supportee sur cette machine^).
exit /b 0
