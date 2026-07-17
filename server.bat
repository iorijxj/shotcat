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
REM netsh portproxy + firewall rules (containers publish on internal
REM ports; a native listener fronts the LAN). That needs admin rights.
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

REM LAN access design: a native Windows portproxy listener terminates LAN
REM connections and relays them into WSL2, so the WSL network never faces
REM the LAN interface directly. The relay target depends on the WSL
REM networking mode:
REM   - NAT (recommended for LAN-serving machines): forward to the WSL
REM     internal IP. The NAT subnet is unreachable from the LAN, and the
REM     physical NIC belongs 100%% to Windows -- no mirrored-mode packet
REM     interference (mirrored shares the host IP with Linux, which was
REM     observed to RST inbound LAN connections on this setup).
REM   - mirrored (fine for localhost-only dev): forward to 127.0.0.1,
REM     which mirrored shares between Windows and WSL.
set "CONNECT_ADDR=127.0.0.1"
set "WSL_NET_MODE=unknown"
for /f "delims=" %%M in ('wsl -- wslinfo --networking-mode 2^>nul') do set "WSL_NET_MODE=%%M"
if /i "!WSL_NET_MODE!"=="nat" (
    for /f "tokens=1" %%I in ('wsl -- hostname -I') do set "CONNECT_ADDR=%%I"
)
echo [server] WSL networking mode: !WSL_NET_MODE!, portproxy target: !CONNECT_ADDR!
echo [server] opening LAN access ^(native portproxy -^> WSL2 containers^)...
REM portproxy listeners live in the IP Helper service; make sure it runs.
sc query iphlpsvc | findstr /i "RUNNING" >nul || net start iphlpsvc >nul 2>&1
for %%Z in ("!SERVER_FRONT_PORT!=!SERVER_FRONT_INTERNAL_PORT!" "!SERVER_BACKEND_PORT!=!SERVER_BACKEND_INTERNAL_PORT!") do (
    for /f "tokens=1,2 delims==" %%A in ("%%~Z") do (
        netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%%A >nul 2>&1
        netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=%%A connectaddress=!CONNECT_ADDR! connectport=%%B >nul
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
echo [server] self-check 1/2: containers on internal ports ^(via !CONNECT_ADDR!^)...
for %%P in (!SERVER_FRONT_INTERNAL_PORT! !SERVER_BACKEND_INTERNAL_PORT!) do call :probe_port !CONNECT_ADDR! %%P
echo [server] self-check 2/2: full chain through the portproxy listener...
for %%P in (!SERVER_FRONT_PORT! !SERVER_BACKEND_PORT!) do call :probe_port 127.0.0.1 %%P

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

REM Probe host %1 port %2 over HTTP, retrying for up to ~30s: nginx answers
REM the moment its container starts, but the backend needs several seconds
REM to connect to MySQL and begin listening, so a single immediate probe
REM produces false FAILs. Any HTTP status (even 404) counts as reachable.
:probe_port
set "HTTP_CODE=000"
set /a PROBE_TRIES=0
:probe_retry
for /f %%C in ('curl -s -o NUL -m 5 -w "%%{http_code}" http://%~1:%~2/ 2^>nul') do set "HTTP_CODE=%%C"
if not "%HTTP_CODE%"=="000" (
    echo [server][OK] http://%~1:%~2/ responded ^(HTTP %HTTP_CODE%^)
    exit /b 0
)
set /a PROBE_TRIES+=1
if %PROBE_TRIES%==1 echo [server] waiting for http://%~1:%~2/ to come up...
if %PROBE_TRIES% LSS 15 (
    timeout /t 2 /nobreak >nul
    goto probe_retry
)
echo [server][FAIL] http://%~1:%~2/ still unreachable after ~30s. Container status:
wsl -- docker ps --format "table {{.Names}}\t{{.Status}}"
set "SELFCHECK_FAIL=1"
exit /b 1
