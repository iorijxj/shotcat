@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - stop.bat
REM Stops everything started by test.bat, run.bat and server.bat:
REM the tagged local terminal windows (backend/front processes)
REM and the docker compose stack. Only kills windows carrying the
REM exact shotcat window-title tags, so unrelated terminals and
REM processes are left untouched.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

echo [stop] === stopping shotcat services (test.bat / run.bat / server.bat) ===

echo [stop] closing tagged terminal windows from test.bat / run.bat...
for %%T in ("shotcat-backend-test" "shotcat-front-test" "shotcat-backend-run" "shotcat-front-run") do (
    taskkill /FI "WINDOWTITLE eq %%~T" /T /F >nul 2>&1
)

echo [stop] stopping docker compose containers ^(mysql/redis/rustfs/backend/celery-worker/front^)...
if exist "%COMPOSE_ENV%" (
    for /f "delims=" %%W in ('wsl -- wslpath -a "%ROOT:~0,-1%"') do set "WSL_ROOT=%%W"
    set "WSL_COMPOSE_DIR=!WSL_ROOT!/app/deploy/compose"
    wsl -- docker compose --env-file "!WSL_COMPOSE_DIR!/.env" -f "!WSL_COMPOSE_DIR!/docker-compose.yml" down
) else (
    echo [stop] %COMPOSE_ENV% not found, skip docker compose down
)

echo [stop] removing LAN port forwarding created by server.bat ^(needs Administrator^)...
net session >nul 2>&1
if errorlevel 1 (
    echo [stop] not running as Administrator, skipped netsh/firewall cleanup for ports 7788/8000. Re-run stop.bat as Administrator if server.bat was used.
) else (
    for %%P in (7788 8000) do (
        netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%%P >nul 2>&1
        netsh advfirewall firewall delete rule name="shotcat-server-%%P" >nul 2>&1
    )
)

echo [stop] === done. Local dev windows closed, containers stopped ^(volumes kept^). ===

:end
endlocal
pause
