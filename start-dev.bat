@echo off
REM ============================================================
REM OpenMap dev demo one-click start/restart (Windows)
REM   backend : WSL uvicorn (demo replay mode, :8000)
REM   frontend: Vite dev server (:5173)
REM Run this script again = restart (stops old services first).
REM Stop a service = close its window.
REM To use real Baidu API: set OPENMAP_BAIDU_AK env var and
REM remove OPENMAP_DEMO_MODE=1 below (line with uvicorn).
REM ============================================================
title OpenMap dev

echo [1/3] Stopping old services...
wsl -e bash -c "pkill -f 'uvicorn app.main:app' 2>/dev/null; exit 0"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1
ping -n 2 127.0.0.1 >nul

echo [2/3] Starting backend (WSL, port 8000, demo mode)...
start "openmap-api" wsl -e bash -c "cd /mnt/d/DockerWSL/openmap/backend && OPENMAP_DEMO_MODE=1 python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000; echo; read -p 'Backend exited. Press Enter to close...'"

echo [3/3] Starting frontend (Vite, port 5173)...
start "openmap-web" cmd /k "cd /d D:\DockerWSL\openmap\frontend && npm run dev"

echo.
echo Waiting for backend health...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 15;$i++){ try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v1/health -TimeoutSec 2 | Out-Null; $ok=$true; break } catch { Start-Sleep 1 } }; if($ok){ Write-Host '  Backend OK (demo mode)' -ForegroundColor Green } else { Write-Host '  Backend NOT ready in 15s, check openmap-api window' -ForegroundColor Red }"

echo.
echo   Frontend : http://localhost:5173   (click the demo button on the right panel)
echo   Backend  : http://localhost:8000/api/v1/health
echo   Logs     : openmap-api / openmap-web windows (close window = stop service)
echo   Restart  : run this script again
echo.
ping -n 7 127.0.0.1 >nul
