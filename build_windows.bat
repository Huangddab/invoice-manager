@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py tools\build_windows.py
) else (
    python tools\build_windows.py
)
if errorlevel 1 (
    echo Build failed. Please keep this output for diagnosis.
    pause
    exit /b 1
)
echo Done: dist\InvoiceManager.exe
pause
