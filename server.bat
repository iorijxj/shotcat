@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - server.bat
REM One-click headless startup: brings up the full docker compose
REM stack (mysql, redis, rustfs, backend, celery-worker, front), builds
REM and serves the web/ workbench, and starts bridge/pipeline_server.py --
REM all in the background so the app is reachable over the network
REM without running any local frontend dev process.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

echo [server] === shotcat full stack startup ^(docker compose, headless^) ===

REM Making the stack reachable from other machines on the LAN requires
REM firewall rules and cleanup of legacy portproxy entries (containers
REM publish on internal ports; a native reverse proxy fronts the LAN).
REM That needs admin rights.
net session >nul 2>&1
if errorlevel 1 (
    echo [server] this script needs Administrator rights to open the LAN-facing ports. Right-click server.bat and choose "Run as administrator".
    goto :end
)

if not exist "%COMPOSE_ENV%" (
    echo [server] %COMPOSE_ENV% not found. Run install.bat first.
    goto :end
)

REM WSL must never idle-shutdown on a serving machine: the VM takes every
REM container with it and comes back on a different NAT IP, so the stack
REM silently dies minutes after startup.
findstr /i "vmIdleTimeout" "%USERPROFILE%\.wslconfig" >nul 2>&1
if errorlevel 1 (
    echo [server][WARN] %USERPROFILE%\.wslconfig is missing "vmIdleTimeout=-1" --
    echo [server][WARN] WSL will auto-shutdown when idle and kill all containers.
    echo [server][WARN] Make sure that file contains these two lines:
    echo [server][WARN]     [wsl2]
    echo [server][WARN]     vmIdleTimeout=-1
    echo [server][WARN] then run "wsl --shutdown" once and re-run server.bat.
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
REM web/ is the day-to-day shotcat workbench (casting/shots/keyframes),
REM a separate Vite app from app/front (the platform's own Studio admin
REM UI). It has no internal Docker port -- Caddy serves its static build
REM directly, so there is no *_INTERNAL_PORT counterpart for it below.
if not defined SERVER_WEB_PORT set "SERVER_WEB_PORT=18081"
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

REM Pin the WSL VM so it can never idle-shutdown: vmIdleTimeout=-1 in
REM .wslconfig proved to be silently ignored on this WSL build (the VM
REM still stopped ~1 min after the last wsl.exe exited, taking every
REM container down and changing the NAT IP). A persistent wsl.exe client
REM handle prevents the idle stop deterministically.
taskkill /FI "WINDOWTITLE eq shotcat-wsl-keepalive" /T /F >nul 2>&1
start "shotcat-wsl-keepalive" /min wsl -- sleep infinity

%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" up -d --build
if errorlevel 1 (
    echo [server] docker compose up failed
    goto :end
)

REM web/ ships as a static build served directly by Caddy below (no
REM container involved), so it must be built before the Caddyfile is
REM written and Caddy is (re)started.
echo [server] building web/ ^(the shotcat workbench^)...
call pnpm --dir "%ROOT%web" install --frozen-lockfile
if errorlevel 1 (
    echo [server] pnpm install failed in web/
    goto :end
)
call pnpm --dir "%ROOT%web" run build
if errorlevel 1 (
    echo [server] pnpm run build failed in web/
    goto :end
)

REM LAN access design: a user-mode native reverse proxy (Caddy, single
REM exe at tools\caddy.exe) listens on the public ports and forwards into
REM WSL2, so the WSL network never faces the LAN interface directly. The
REM forward target depends on the WSL networking mode:
REM   - NAT (recommended for LAN-serving machines): the WSL internal IP.
REM   - mirrored (fine for localhost-only dev): 127.0.0.1.
REM Why Caddy and not netsh portproxy: the iphlpsvc kernel relay poisons
REM WinNAT state when piping LAN-originated connections into the WSL NAT
REM subnet (first request succeeds, later ones get RST), while a
REM user-mode native listener is stable on the exact same path. Note the
REM host's localhostForwarding is also broken on some machines, so under
REM NAT the target must be the WSL IP, never 127.0.0.1.
set "CONNECT_ADDR=127.0.0.1"
set "WSL_NET_MODE=unknown"
for /f "delims=" %%M in ('wsl -- wslinfo --networking-mode 2^>nul') do set "WSL_NET_MODE=%%M"
if /i "!WSL_NET_MODE!"=="nat" (
    for /f "tokens=1" %%I in ('wsl -- hostname -I') do set "CONNECT_ADDR=%%I"
)
echo [server] WSL networking mode: !WSL_NET_MODE!, forward target: !CONNECT_ADDR!

if not exist "%ROOT%tools\caddy.exe" (
    echo [server] tools\caddy.exe not found -- it is the LAN-facing reverse proxy.
    echo [server] Download the Windows amd64 build from:
    echo [server]     https://caddyserver.com/api/download?os=windows^&arch=amd64
    echo [server] rename it to caddy.exe, put it at %ROOT%tools\caddy.exe and re-run server.bat.
    goto :end
)

REM Legacy cleanup (earlier versions used netsh portproxy on these ports),
REM plus the firewall openings for the public ports.
for %%P in (!SERVER_FRONT_PORT! !SERVER_BACKEND_PORT! !SERVER_WEB_PORT!) do (
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%%P >nul 2>&1
    netsh advfirewall firewall show rule name="shotcat-server-%%P" >nul 2>&1
    if errorlevel 1 (
        netsh advfirewall firewall add rule name="shotcat-server-%%P" dir=in action=allow protocol=TCP localport=%%P >nul
    )
)

set "CADDYFILE=%TEMP%\shotcat-caddyfile"
> "%CADDYFILE%" echo # generated by server.bat -- do not edit
>>"%CADDYFILE%" echo {
>>"%CADDYFILE%" echo     auto_https off
>>"%CADDYFILE%" echo     admin off
>>"%CADDYFILE%" echo }
>>"%CADDYFILE%" echo :!SERVER_FRONT_PORT! {
>>"%CADDYFILE%" echo     reverse_proxy !CONNECT_ADDR!:!SERVER_FRONT_INTERNAL_PORT!
>>"%CADDYFILE%" echo }
>>"%CADDYFILE%" echo :!SERVER_BACKEND_PORT! {
>>"%CADDYFILE%" echo     reverse_proxy !CONNECT_ADDR!:!SERVER_BACKEND_INTERNAL_PORT!
>>"%CADDYFILE%" echo }
REM web/ calls its own backend/pipeline through same-origin relative paths
REM (/api/*, /pipeline/*), so its site block serves the static build and
REM path-routes those two prefixes instead of a single blanket proxy.
REM try_files falls back to index.html for anything else -- web/ uses
REM react-router's BrowserRouter (history mode), so a bare file_server
REM would 404 on refreshing any deep client-side route.
>>"%CADDYFILE%" echo :!SERVER_WEB_PORT! {
>>"%CADDYFILE%" echo     root * "!ROOT!web\dist"
>>"%CADDYFILE%" echo     reverse_proxy /api/* !CONNECT_ADDR!:!SERVER_BACKEND_INTERNAL_PORT!
>>"%CADDYFILE%" echo     reverse_proxy /pipeline/* 127.0.0.1:5280
>>"%CADDYFILE%" echo     try_files {path} /index.html
>>"%CADDYFILE%" echo     file_server
>>"%CADDYFILE%" echo }
REM Loopback-only: bridge/*.py (incl. pipeline_server.py) hardcode
REM http://localhost:8000 as their backend base URL. The docker backend
REM does not publish host port 8000, so this bridges that expectation to
REM the real internal port without touching any bridge/ source file. Not
REM opened on the firewall -- "bind 127.0.0.1" keeps it off the LAN.
REM (Writing the address as "127.0.0.1:8000 { ... }" instead of "bind"
REM looks equivalent but is not: Caddy only uses a literal-IP host for
REM Host-header matching, still listens on 0.0.0.0, and -- because the
REM host looks like a TLS-capable address -- attaches a TLS connection
REM policy even with auto_https off, so plain HTTP gets rejected with
REM "Client sent an HTTP request to an HTTPS server".)
>>"%CADDYFILE%" echo :8000 {
>>"%CADDYFILE%" echo     bind 127.0.0.1
>>"%CADDYFILE%" echo     reverse_proxy !CONNECT_ADDR!:!SERVER_BACKEND_INTERNAL_PORT!
>>"%CADDYFILE%" echo }

echo [server] starting the LAN-facing reverse proxy ^(caddy^) in window "shotcat-caddy"...
taskkill /FI "WINDOWTITLE eq shotcat-caddy" /T /F >nul 2>&1
start "shotcat-caddy" /min "%ROOT%tools\caddy.exe" run --config "%CADDYFILE%" --adapter caddyfile

REM web/'s "lock visual dictionary / shot breakdown / narration unit"
REM features call bridge/pipeline_server.py, fixed on 127.0.0.1:5280.
where python >nul 2>&1
if errorlevel 1 (
    echo [server][WARN] python not found on PATH -- skipping bridge/pipeline_server.py.
    echo [server][WARN] web/'s visual dictionary / shot breakdown / narration unit features will not work until it is started manually.
) else (
    echo [server] starting bridge/pipeline_server.py in window "shotcat-pipeline"...
    taskkill /FI "WINDOWTITLE eq shotcat-pipeline" /T /F >nul 2>&1
    start "shotcat-pipeline" /min python "%ROOT%bridge\pipeline_server.py"
)

REM Self-check: probe each hop so a broken link is visible immediately
REM instead of being discovered later from another machine. Any HTTP
REM status (even 404) counts as OK -- only 000 means the connection failed.
set "SELFCHECK_FAIL="
echo [server] self-check 1/2: containers on internal ports ^(via !CONNECT_ADDR!^)...
for %%P in (!SERVER_FRONT_INTERNAL_PORT! !SERVER_BACKEND_INTERNAL_PORT!) do call :probe_port !CONNECT_ADDR! %%P
echo [server] self-check 2/2: full chain through the reverse proxy...
for %%P in (!SERVER_FRONT_PORT! !SERVER_BACKEND_PORT! !SERVER_WEB_PORT!) do call :probe_port 127.0.0.1 %%P

if defined SELFCHECK_FAIL (
    echo [server] === stack started but the self-check FAILED, LAN access will not work until this is fixed ===
    goto :end
)
echo [server] === full stack is up, self-check passed ===
echo [server] workbench ^(web/, day-to-day use^): http://localhost:%SERVER_WEB_PORT%
echo [server] Studio admin ^(app/front^): http://localhost:%SERVER_FRONT_PORT%
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
