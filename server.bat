@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - server.bat
REM One-click headless startup: brings up the full docker compose
REM stack (mysql, redis, rustfs, backend, celery-worker, front) in
REM the background so the app is reachable over the network
REM without running any local frontend dev process.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

echo [server] === shotcat full stack startup ^(docker compose, headless^) ===

REM Making the stack reachable from other machines on the LAN requires
REM netsh portproxy + firewall rules (WSL2's NAT only forwards localhost
REM traffic on its own, unlike Docker Desktop). That needs admin rights.
net session >nul 2>&1
if errorlevel 1 (
    echo [server] this script needs Administrator rights to open the LAN-facing ports. Right-click server.bat and choose "Run as administrator".
    goto :end
)

if not exist "%COMPOSE_ENV%" (
    echo [server] %COMPOSE_ENV% not found. Run install.bat first.
    goto :end
)

REM Read the actual published ports from .env. These are deliberately
REM different from test.bat/run.bat's native dev ports (8000/7788) so the
REM two modes can never fight over the same port.
for /f "usebackq tokens=1,2 delims==" %%A in ("%COMPOSE_ENV%") do (
    set "key=%%A"
    if not "!key:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)
if not defined SERVER_BACKEND_PORT set "SERVER_BACKEND_PORT=18000"
if not defined SERVER_FRONT_PORT set "SERVER_FRONT_PORT=18080"

REM Docker Desktop is not allowed here; the stack runs via Docker Engine
REM inside WSL2 (set up by install.bat).
for /f "delims=" %%W in ('wsl -- wslpath -a "%ROOT:~0,-1%"') do set "WSL_ROOT=%%W"
set "WSL_COMPOSE_DIR=%WSL_ROOT%/app/deploy/compose"
set "WSL_COMPOSE_ENV=%WSL_COMPOSE_DIR%/.env"
set "WSL_COMPOSE_FILE=%WSL_COMPOSE_DIR%/docker-compose.yml"
set "DOCKER=wsl -- docker"

%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" up -d --build
if errorlevel 1 (
    echo [server] docker compose up failed
    goto :end
)

echo [server] opening LAN access to the WSL2-hosted stack...
for /f "tokens=1" %%I in ('wsl -- hostname -I') do set "WSL_IP=%%I"
if not defined WSL_IP (
    echo [server] could not determine the WSL IP, skipping LAN port forwarding. The stack is still reachable from this machine at localhost.
    goto skip_portproxy
)

for %%P in (!SERVER_FRONT_PORT! !SERVER_BACKEND_PORT!) do (
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%%P >nul 2>&1
    netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=%%P connectaddress=!WSL_IP! connectport=%%P >nul
    netsh advfirewall firewall show rule name="shotcat-server-%%P" >nul 2>&1
    if errorlevel 1 (
        netsh advfirewall firewall add rule name="shotcat-server-%%P" dir=in action=allow protocol=TCP localport=%%P >nul
    )
)
:skip_portproxy

echo [server] === full stack is up ===
echo [server] frontend ^(built^): http://localhost:%SERVER_FRONT_PORT%
echo [server] backend docs: http://localhost:%SERVER_BACKEND_PORT%/docs
echo [server] other machines on the same network can reach it via this host's LAN IP on the same ports.

:end
endlocal
pause
