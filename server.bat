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
REM netsh portproxy + firewall rules (containers publish on 127.0.0.1
REM only; a native listener fronts the LAN). That needs admin rights.
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
REM Internal ports: what the containers publish on 127.0.0.1. These MUST
REM differ from the public ports above -- WSL2 mirrored networking tracks
REM bound ports machine-wide by port number, and reusing the same number
REM for the portproxy listener makes LAN-facing connections get refused.
if not defined SERVER_BACKEND_INTERNAL_PORT set "SERVER_BACKEND_INTERNAL_PORT=28000"
if not defined SERVER_FRONT_INTERNAL_PORT set "SERVER_FRONT_INTERNAL_PORT=28080"

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

REM LAN access design: the containers publish their ports on 127.0.0.1
REM only (see docker-compose.yml), so the WSL2 mirrored network never
REM faces the LAN interface directly -- that direct path fights with
REM VPN/Tun virtual adapters (e.g. v2rayN Tun mode) and drops connections
REM intermittently. Instead a native Windows portproxy listener terminates
REM LAN connections and relays them over loopback into WSL2, which is the
REM one path proven stable on this setup.
echo [server] opening LAN access ^(native portproxy -^> loopback -^> WSL2^)...
REM portproxy listeners live in the IP Helper service; make sure it runs.
sc query iphlpsvc | findstr /i "RUNNING" >nul || net start iphlpsvc >nul 2>&1
for %%Z in ("!SERVER_FRONT_PORT!=!SERVER_FRONT_INTERNAL_PORT!" "!SERVER_BACKEND_PORT!=!SERVER_BACKEND_INTERNAL_PORT!") do (
    for /f "tokens=1,2 delims==" %%A in ("%%~Z") do (
        netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%%A >nul 2>&1
        netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=%%A connectaddress=127.0.0.1 connectport=%%B >nul
        netsh advfirewall firewall show rule name="shotcat-server-%%A" >nul 2>&1
        if errorlevel 1 (
            netsh advfirewall firewall add rule name="shotcat-server-%%A" dir=in action=allow protocol=TCP localport=%%A >nul
        )
    )
)

REM Self-check: print the actual portproxy table and probe each port over
REM loopback so a broken link is visible immediately instead of being
REM discovered later from another machine. Any HTTP status (even 404)
REM counts as OK -- only 000 means the connection itself failed.
echo [server] active portproxy rules:
netsh interface portproxy show v4tov4
set "SELFCHECK_FAIL="
echo [server] self-check 1/2: containers on internal loopback ports...
for %%P in (!SERVER_FRONT_INTERNAL_PORT! !SERVER_BACKEND_INTERNAL_PORT!) do call :probe_port %%P
echo [server] self-check 2/2: full chain through the portproxy listener...
for %%P in (!SERVER_FRONT_PORT! !SERVER_BACKEND_PORT!) do call :probe_port %%P

if defined SELFCHECK_FAIL (
    echo [server] === stack started but the self-check FAILED, LAN access will not work until this is fixed ===
    goto :end
)
echo [server] === full stack is up, self-check passed ===
echo [server] frontend ^(built^): http://localhost:%SERVER_FRONT_PORT%
echo [server] backend docs: http://localhost:%SERVER_BACKEND_PORT%/docs
echo [server] other machines on the same network can reach it via this host's LAN IP on the same ports.

:end
endlocal
pause
goto :eof

REM Probe one port over loopback, retrying for up to ~30s: nginx answers
REM the moment its container starts, but the backend needs several seconds
REM to connect to MySQL and begin listening, so a single immediate probe
REM produces false FAILs. Any HTTP status (even 404) counts as reachable.
:probe_port
set "HTTP_CODE=000"
set /a PROBE_TRIES=0
:probe_retry
for /f %%C in ('curl -s -o NUL -m 5 -w "%%{http_code}" http://127.0.0.1:%~1/ 2^>nul') do set "HTTP_CODE=%%C"
if not "%HTTP_CODE%"=="000" (
    echo [server][OK] http://127.0.0.1:%~1/ responded ^(HTTP %HTTP_CODE%^)
    exit /b 0
)
set /a PROBE_TRIES+=1
if %PROBE_TRIES%==1 echo [server] waiting for http://127.0.0.1:%~1/ to come up...
if %PROBE_TRIES% LSS 15 (
    timeout /t 2 /nobreak >nul
    goto probe_retry
)
echo [server][FAIL] http://127.0.0.1:%~1/ still unreachable after ~30s. Container status:
wsl -- docker ps --format "table {{.Names}}\t{{.Status}}"
set "SELFCHECK_FAIL=1"
exit /b 1
