@echo off
cd /d "%~dp0"

rem Verified portable Python 3.12 runtime on this machine
set "PY=C:\Users\LENOVO\Documents\Codex\2026-10-30\x20\outputs\demo-env\runtime\python\python.exe"
set "PYTHONPATH=C:\Users\LENOVO\Documents\Codex\2026-10-30\x20\outputs\demo-env\runtime\pylib"

if not exist "%PY%" goto missing

echo ============================================================
echo Medical Triage backend
echo URL:    http://127.0.0.1:8000
echo Health: http://127.0.0.1:8000/api/v1/health
echo Keep this window open. Close it to stop the backend.
echo ============================================================
echo.

"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Backend stopped.
pause
exit /b 0

:missing
echo.
echo [ERROR] Python runtime not found:
echo %PY%
echo.
echo Install dependencies and run:
echo   python -m pip install -r requirements.txt -r requirements-dev.txt
echo   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
pause
exit /b 1
