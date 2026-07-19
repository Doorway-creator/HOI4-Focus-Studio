@echo off
cd /d "%~dp0"

echo Starting HOI4 Focus Studio v6.9.0 DEV9 on port 8766...

where py >nul 2>nul
if not errorlevel 1 (
    py -3 server.py
    if errorlevel 1 pause
    exit /b
)

where python >nul 2>nul
if not errorlevel 1 (
    python server.py
    if errorlevel 1 pause
    exit /b
)

echo Python was not found. Install Python 3 or restore the Codex runtime.
pause
