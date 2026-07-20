@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - server.bat
REM One-click headless startup: brings up the docker compose stack
REM (mysql, redis, rustfs, backend, celery-worker -- app/front is legacy
REM and stays behind the "legacy" compose profile, not started here),
REM builds and serves the web/ workbench, and starts
REM bridge/pipeline_server.py -- all in the background so the app is
REM reachable over the network without running any local frontend dev
REM process.
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
REM different from test.bat/run.bat's native dev ports (8000/5273) so the
REM two modes can never fight over the same port.
for /f "usebackq tokens=1,2 delims==" %%A in ("%COMPOSE_ENV%") do (
    set "key=%%A"
    if not "!key:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)
if not defined SERVER_BACKEND_PORT set "SERVER_BACKEND_PORT=18000"
REM web/ is the day-to-day shotcat workbench (casting/shots/keyframes) and
REM the only frontend this script exposes. app/front (the platform's own
REM Studio admin UI) is legacy-maintenance-only (see the 2026-07-19
REM frontend consolidation doc under docs/):
REM its docker-compose service is behind the "legacy" profile, so the
REM `docker compose up -d --build` below never starts it, and this script
REM does not open a LAN-facing port or Caddy route for it. web/ has no
REM internal Docker port -- Caddy serves its static build directly, so
REM there is no *_INTERNAL_PORT counterpart for it below.
if not defined SERVER_WEB_PORT set "SERVER_WEB_PORT=18081"
REM Internal ports: what the containers publish on 127.0.0.1. These MUST
REM differ from the public ports above -- WSL2 mirrored networking tracks
REM bound ports machine-wide by port number, and reusing the same number
REM for the portproxy listener makes LAN-facing connections get refused.
if not defined SERVER_BACKEND_INTERNAL_PORT set "SERVER_BACKEND_INTERNAL_PORT=28000"

REM Caddy 网关加固（安全整改阶段一 1.2/1.3）：进门口令必填，缺失就不启动，
REM 避免网关裸奔上线。PUBLIC_DOMAIN 留空 = 纯局域网模式（tls internal 自签证书），
REM 填了真实域名 = Caddy 走自动 HTTPS（Let's Encrypt）。
if "!CADDY_BASIC_AUTH_USER!"=="" (
    echo [server] CADDY_BASIC_AUTH_USER is empty in %COMPOSE_ENV% -- set a login for the Caddy gateway before serving.
    goto :end
)
if "!CADDY_BASIC_AUTH_PASSWORD!"=="" (
    echo [server] CADDY_BASIC_AUTH_PASSWORD is empty in %COMPOSE_ENV% -- set a login for the Caddy gateway before serving.
    goto :end
)
set "HOST_PREFIX=!PUBLIC_DOMAIN!"

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

REM Use the images install.bat pre-built; deliberately NOT --build. This
REM machine's proxy truncates registry pulls (auth.docker.io EOF), so
REM resolving the base image at serve-time is unreliable. install.bat owns
REM image builds -- after changing backend code, re-run install.bat to
REM rebuild the images before serving.
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" up -d
if errorlevel 1 (
    echo [server] docker compose up failed ^(images missing? run install.bat first^)
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

REM Hash the basicauth password fresh on every start (never written to disk in
REM plaintext outside .env) so the generated Caddyfile only ever holds a bcrypt hash.
REM The whole command is wrapped in an extra ^"...^" pair: for /f runs its command
REM through an inner cmd /c, and when that command starts with a quote (the quoted
REM caddy.exe path) cmd strips the first and last quote of the ENTIRE line, corrupting
REM the exe name into "...caddy.exe^"" and making it "not recognized" (hash comes back
REM empty -> the abort below). The extra outer pair is what cmd strips, leaving the
REM real quoting intact.
for /f "delims=" %%H in ('^""%ROOT%tools\caddy.exe" hash-password --plaintext "!CADDY_BASIC_AUTH_PASSWORD!" 2^>nul^"') do set "CADDY_BASIC_AUTH_HASH=%%H"
if not defined CADDY_BASIC_AUTH_HASH (
    echo [server] failed to hash CADDY_BASIC_AUTH_PASSWORD via caddy.exe -- aborting.
    goto :end
)
REM No PUBLIC_DOMAIN -- bare-port LAN sites need an explicit issuer since
REM auto_https can't request a public cert for a hostname-less address.
if "!HOST_PREFIX!"=="" (
    set "TLS_DIRECTIVE=tls internal"
) else (
    set "TLS_DIRECTIVE="
)

REM Legacy cleanup (earlier versions used netsh portproxy on these ports),
REM plus the firewall openings for the public ports.
for %%P in (!SERVER_BACKEND_PORT! !SERVER_WEB_PORT!) do (
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%%P >nul 2>&1
    netsh advfirewall firewall show rule name="shotcat-server-%%P" >nul 2>&1
    if errorlevel 1 (
        netsh advfirewall firewall add rule name="shotcat-server-%%P" dir=in action=allow protocol=TCP localport=%%P >nul
    )
)

set "CADDYFILE=%TEMP%\shotcat-caddyfile"
> "%CADDYFILE%" echo # generated by server.bat -- do not edit
>>"%CADDYFILE%" echo {
>>"%CADDYFILE%" echo     admin off
>>"%CADDYFILE%" echo }
REM basic_auth gates the whole site; /docs, /redoc, /openapi.json are denied
REM before basic_auth even runs, so they 403 unconditionally instead of just
REM requiring a login (security review P0 items 1.2 and 1.4).
>>"%CADDYFILE%" echo !HOST_PREFIX!:!SERVER_BACKEND_PORT! {
REM "if defined" guards this line -- an empty !TLS_DIRECTIVE! (public-domain
REM mode) would make the line just "echo" with trailing spaces, which cmd
REM special-cases into printing "ECHO is off." instead of a blank line.
if defined TLS_DIRECTIVE >>"%CADDYFILE%" echo     !TLS_DIRECTIVE!
>>"%CADDYFILE%" echo     route {
>>"%CADDYFILE%" echo         @denied path /docs /redoc /openapi.json
>>"%CADDYFILE%" echo         respond @denied 403
>>"%CADDYFILE%" echo         basic_auth {
>>"%CADDYFILE%" echo             !CADDY_BASIC_AUTH_USER! !CADDY_BASIC_AUTH_HASH!
>>"%CADDYFILE%" echo         }
REM Upload size backstop (security stage 3, item 3.2): the backend enforces
REM the precise per-type limits (2MB image / 5MB video); this gateway-level
REM cap (limit + multipart overhead headroom) just stops oversized bodies
REM from ever reaching the backend.
>>"%CADDYFILE%" echo         @upload path /api/v1/studio/files/upload
>>"%CADDYFILE%" echo         request_body @upload {
>>"%CADDYFILE%" echo             max_size 10MB
>>"%CADDYFILE%" echo         }
>>"%CADDYFILE%" echo         reverse_proxy !CONNECT_ADDR!:!SERVER_BACKEND_INTERNAL_PORT!
>>"%CADDYFILE%" echo     }
>>"%CADDYFILE%" echo }
REM web/ calls its own backend/pipeline through same-origin relative paths
REM (/api/*, /pipeline/*), so its site block serves the static build and
REM path-routes those two prefixes instead of a single blanket proxy.
REM try_files falls back to index.html for anything else -- web/ uses
REM react-router's BrowserRouter (history mode), so a bare file_server
REM would 404 on refreshing any deep client-side route.
REM The "route { }" wrapper is required, not cosmetic: Caddy reorders
REM bare top-level directives by its own fixed category order, and
REM try_files' fallback to /index.html always matches (the file always
REM exists), so without "route" it runs before reverse_proxy ever sees
REM the original path -- every /api/* and /pipeline/* request silently
REM falls through to file_server instead (GET gets index.html's HTML
REM back instead of JSON, POST/PUT/DELETE get a bare 405 since
REM file_server only serves GET/HEAD). "route" forces this block to
REM run in the exact order written below. /docs, /redoc, /openapi.json
REM are never proxied here (only /api/* and /pipeline/* are), so they
REM already fall through to the SPA's index.html -- no separate 403 rule
REM is needed on this site the way the backend site above needs one.
>>"%CADDYFILE%" echo !HOST_PREFIX!:!SERVER_WEB_PORT! {
if defined TLS_DIRECTIVE >>"%CADDYFILE%" echo     !TLS_DIRECTIVE!
>>"%CADDYFILE%" echo     root * "!ROOT!web\dist"
>>"%CADDYFILE%" echo     route {
>>"%CADDYFILE%" echo         basic_auth {
>>"%CADDYFILE%" echo             !CADDY_BASIC_AUTH_USER! !CADDY_BASIC_AUTH_HASH!
>>"%CADDYFILE%" echo         }
REM Same upload size backstop as the backend site above -- the workbench
REM uploads through this site's /api/* proxy, so both entrances need it.
>>"%CADDYFILE%" echo         @upload path /api/v1/studio/files/upload
>>"%CADDYFILE%" echo         request_body @upload {
>>"%CADDYFILE%" echo             max_size 10MB
>>"%CADDYFILE%" echo         }
>>"%CADDYFILE%" echo         reverse_proxy /api/* !CONNECT_ADDR!:!SERVER_BACKEND_INTERNAL_PORT!
>>"%CADDYFILE%" echo         reverse_proxy /pipeline/* 127.0.0.1:5280
>>"%CADDYFILE%" echo         try_files {path} /index.html
>>"%CADDYFILE%" echo         file_server
>>"%CADDYFILE%" echo     }
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
REM "Client sent an HTTP request to an HTTPS server". The explicit
REM "http://" scheme below is required for a separate reason now that
REM the two LAN-facing sites above use "tls internal": without it, this
REM bare-port, no-TLS site collides with their catch-all automation
REM policy and Caddy refuses to start ("automation policy from site
REM block is also default/catch-all policy ... in conflict").)
>>"%CADDYFILE%" echo http://:8000 {
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
for %%P in (!SERVER_BACKEND_INTERNAL_PORT!) do call :probe_port !CONNECT_ADDR! %%P
echo [server] self-check 2/2: full chain through the reverse proxy...
for %%P in (!SERVER_BACKEND_PORT! !SERVER_WEB_PORT!) do call :probe_port 127.0.0.1 %%P https

if defined SELFCHECK_FAIL (
    echo [server] === stack started but the self-check FAILED, LAN access will not work until this is fixed ===
    goto :end
)
echo [server] === full stack is up, self-check passed ===
echo [server] workbench ^(web/, day-to-day use^): https://localhost:%SERVER_WEB_PORT% ^(login required^)
echo [server] backend API: https://localhost:%SERVER_BACKEND_PORT% ^(login required; /docs, /redoc, /openapi.json return 403 on purpose^)
if "!HOST_PREFIX!"=="" echo [server] no PUBLIC_DOMAIN set -- these use a self-signed cert, browsers will warn once per client.
echo [server] app/front ^(legacy Studio, not started/exposed by default^): docker compose --profile legacy up -d --build front
echo [server] other machines on the same network can reach it via this host's LAN IP on the same ports.

:end
endlocal
pause
goto :eof

REM Probe host %1 port %2 over HTTP (or HTTPS if %3=https, skipping cert
REM checks since the LAN default is a self-signed "tls internal" cert),
REM retrying for up to ~30s: nginx answers the moment its container starts,
REM but the backend needs several seconds to connect to MySQL and begin
REM listening, so a single immediate probe produces false FAILs. Any HTTP
REM status (even 404, or 401 from basic_auth) counts as reachable.
:probe_port
set "PROBE_SCHEME=%~3"
if "%PROBE_SCHEME%"=="" set "PROBE_SCHEME=http"
set "CURL_INSECURE="
if /i "%PROBE_SCHEME%"=="https" set "CURL_INSECURE=-k"
set "HTTP_CODE=000"
set /a PROBE_TRIES=0
:probe_retry
for /f %%C in ('curl -s %CURL_INSECURE% -o NUL -m 5 -w "%%{http_code}" %PROBE_SCHEME%://%~1:%~2/ 2^>nul') do set "HTTP_CODE=%%C"
if not "%HTTP_CODE%"=="000" (
    echo [server][OK] %PROBE_SCHEME%://%~1:%~2/ responded ^(HTTP %HTTP_CODE%^)
    exit /b 0
)
set /a PROBE_TRIES+=1
if %PROBE_TRIES%==1 echo [server] waiting for %PROBE_SCHEME%://%~1:%~2/ to come up...
if %PROBE_TRIES% LSS 15 (
    timeout /t 2 /nobreak >nul
    goto probe_retry
)
echo [server][FAIL] %PROBE_SCHEME%://%~1:%~2/ still unreachable after ~30s. Container status:
wsl -- docker ps --format "table {{.Names}}\t{{.Status}}"
set "SELFCHECK_FAIL=1"
exit /b 1
