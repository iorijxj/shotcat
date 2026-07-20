@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - install.bat
REM One-click environment setup: prerequisite tools, env files,
REM infra containers (MySQL/Redis/RustFS), backend deps, frontend
REM deps, database schema/seed data, object storage bucket.
REM Safe to re-run: every step is idempotent or copy-if-missing.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "BACKEND_DIR=%APP_DIR%\backend"
set "WEB_DIR=%ROOT%web"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

echo [install] === shotcat environment setup ===

REM ---- 1. Check / install prerequisite tools ----
echo [install] checking prerequisite tools...

where winget >nul 2>&1
if errorlevel 1 (
    set "HAS_WINGET=0"
    echo [install] winget not found on this machine, will fall back to manual instructions / direct installers where possible.
) else (
    set "HAS_WINGET=1"
)

REM Docker Desktop is not allowed here, so the container runtime is plain
REM Docker Engine (CLI-only, Apache-2.0) running inside WSL2 instead. All
REM docker/docker compose calls below go through "wsl -- docker ...".

where wsl >nul 2>&1
if errorlevel 1 (
    echo [install] wsl.exe not found. This machine does not have WSL2. Enable it manually: https://learn.microsoft.com/windows/wsl/install
    goto :end
)

wsl -l -q >nul 2>&1
if errorlevel 1 (
    echo [install] No WSL distro is installed yet. Run "wsl --install" as Administrator ^(reboot if it asks^), then re-run install.bat.
    goto :end
)

REM Without vmIdleTimeout=-1 in .wslconfig (not guaranteed to exist yet --
REM that is why installing this pin can't wait until after the docker
REM steps below), WSL2 can idle-shutdown between two separate wsl.exe
REM invocations from this same script, taking the freshly-installed
REM Docker Engine and every container down with it mid-install. A
REM persistent wsl.exe client handle prevents that deterministically
REM (same fix server.bat uses for the same problem).
taskkill /FI "WINDOWTITLE eq shotcat-wsl-keepalive" /T /F >nul 2>&1
start "shotcat-wsl-keepalive" /min wsl -- sleep infinity

wsl -- bash -lc "which docker" >nul 2>&1
if errorlevel 1 (
    echo [install] Docker Engine not found inside WSL. Installing it there ^(no Docker Desktop, CLI-only^)...
    wsl -- bash -lc "curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $(whoami)"
    if errorlevel 1 (
        echo [install] Docker Engine install inside WSL failed. See output above.
        goto :end
    )
    wsl --shutdown
    REM wsl --shutdown just killed the keepalive from above along with
    REM everything else in the VM -- restart it before doing anything more.
    start "shotcat-wsl-keepalive" /min wsl -- sleep infinity
    echo [install] Docker Engine installed inside WSL and the WSL session was restarted to pick up the new docker group membership.
) else (
    echo [install] docker ^(inside WSL^) OK
)

wsl -- bash -lc "docker info" >nul 2>&1
if errorlevel 1 (
    echo [install] Docker daemon inside WSL is not reachable. Try: wsl -- sudo service docker start
    goto :end
)

REM Translate this project's Windows path to the equivalent WSL path so
REM docker compose (running inside WSL) can resolve the compose file,
REM its build context and its bind mounts consistently.
for /f "delims=" %%W in ('wsl -- wslpath -a "%ROOT:~0,-1%"') do set "WSL_ROOT=%%W"
set "WSL_COMPOSE_DIR=%WSL_ROOT%/app/deploy/compose"
set "WSL_COMPOSE_ENV=%WSL_COMPOSE_DIR%/.env"
set "WSL_COMPOSE_FILE=%WSL_COMPOSE_DIR%/docker-compose.yml"
REM Dev override: publishes mysql/redis/rustfs on 127.0.0.1 for the native
REM dev flow (base compose publishes no host ports at all; see the file).
set "WSL_COMPOSE_DEV_FILE=%WSL_COMPOSE_DIR%/docker-compose.dev.yml"
set "DOCKER=wsl -- docker"

where uv >nul 2>&1
if errorlevel 1 (
    if "!HAS_WINGET!"=="1" (
        echo [install] uv not found. Installing via winget...
        winget install -e --id astral-sh.uv --silent --accept-package-agreements --accept-source-agreements
    ) else (
        echo [install] uv not found. Installing via the official install script...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    )
    if errorlevel 1 (
        echo [install] uv install failed. Please install it manually: https://docs.astral.sh/uv/getting-started/installation/
        goto :end
    )
    echo [install] uv installed. Close and reopen this terminal to pick up the updated PATH, then re-run install.bat.
    goto :end
) else (
    echo [install] uv OK
)

where node >nul 2>&1
if errorlevel 1 (
    if "!HAS_WINGET!"=="1" (
        echo [install] Node.js not found. Installing via winget...
        winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
        if errorlevel 1 (
            echo [install] winget install of Node.js failed. Please install it manually from https://nodejs.org/
            goto :end
        )
        echo [install] Node.js installed. Please close and reopen this terminal to pick up the updated PATH, then re-run install.bat.
    ) else (
        echo [install] Node.js not found and winget is unavailable. Please install it manually from https://nodejs.org/ then re-run install.bat.
    )
    goto :end
) else (
    echo [install] node OK
)

where pnpm >nul 2>&1
if errorlevel 1 (
    echo [install] pnpm not found. Installing via npm...
    call npm install -g pnpm
) else (
    echo [install] pnpm OK
)

REM ---- 2. Env files ----
echo [install] preparing env files...

if not exist "%COMPOSE_ENV%" (
    copy /y "%COMPOSE_DIR%\.env.example" "%COMPOSE_ENV%" >nul
    echo [install] created %COMPOSE_ENV% from example
) else (
    echo [install] %COMPOSE_ENV% already exists, skip
)

REM Read the actual infra credentials so backend\.env stays in sync with
REM whatever is really in deploy\compose\.env (not just the example defaults).
for /f "usebackq tokens=1,2 delims==" %%A in ("%COMPOSE_ENV%") do (
    set "key=%%A"
    if not "!key:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)
if not defined MYSQL_USER set "MYSQL_USER=jellyfish"
if not defined MYSQL_PASSWORD set "MYSQL_PASSWORD=change-me"
if not defined MYSQL_DATABASE set "MYSQL_DATABASE=jellyfish"
if not defined MYSQL_PORT set "MYSQL_PORT=3306"
if not defined REDIS_PORT set "REDIS_PORT=6379"
if not defined REDIS_PASSWORD set "REDIS_PASSWORD=change-me"
if not defined RUSTFS_PORT set "RUSTFS_PORT=9000"
if not defined RUSTFS_ACCESS_KEY set "RUSTFS_ACCESS_KEY=rustfsadmin"
if not defined RUSTFS_SECRET_KEY set "RUSTFS_SECRET_KEY=rustfsadmin"
if not defined S3_BUCKET_NAME set "S3_BUCKET_NAME=jellyfish-assets"

if not exist "%BACKEND_DIR%\.env" (
    copy /y "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
    (
        echo.
        echo # ---- appended by install.bat: local dev against dockerized infra ----
        echo DATABASE_URL=mysql+aiomysql://!MYSQL_USER!:!MYSQL_PASSWORD!@localhost:!MYSQL_PORT!/!MYSQL_DATABASE!
        echo REDIS_HOST=localhost
        echo REDIS_PORT=!REDIS_PORT!
        echo REDIS_DB=0
        echo REDIS_PASSWORD=!REDIS_PASSWORD!
        echo CORS_ORIGINS=http://localhost:5273,http://127.0.0.1:5273
        echo S3_ENDPOINT_URL=http://localhost:!RUSTFS_PORT!
        echo S3_REGION_NAME=us-east-1
        echo S3_ACCESS_KEY_ID=!RUSTFS_ACCESS_KEY!
        echo S3_SECRET_ACCESS_KEY=!RUSTFS_SECRET_KEY!
        echo S3_BUCKET_NAME=!S3_BUCKET_NAME!
        echo # 本机防火墙内开发对接本地 docker 基础设施（弱口令合法），显式豁免弱口令校验。
        echo ALLOW_WEAK_SECRETS=true
    ) >> "%BACKEND_DIR%\.env"
    echo [install] created %BACKEND_DIR%\.env from example, pointed at local docker infra
) else (
    echo [install] %BACKEND_DIR%\.env already exists, skip
)

REM ---- 3. Start infra containers ----
echo [install] starting infra containers ^(mysql/redis/rustfs^)...
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" -f "%WSL_COMPOSE_DEV_FILE%" up -d mysql redis rustfs
if errorlevel 1 (
    echo [install] failed to start infra containers
    goto :end
)

echo [install] waiting for mysql to become healthy...
set /a WAIT_SECONDS=0
:wait_mysql
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" ps mysql | findstr /i "healthy" >nul 2>&1
if not errorlevel 1 goto mysql_ready
set /a WAIT_SECONDS+=2
if !WAIT_SECONDS! GEQ 120 (
    echo [install] mysql did not become healthy within 120s, aborting
    goto :end
)
timeout /t 2 /nobreak >nul
goto wait_mysql
:mysql_ready
echo [install] mysql is healthy

REM Docker's health check only confirms mysqld is ready *inside* WSL. The
REM Windows-host localhost:%MYSQL_PORT% forward is a separate WSL2 relay
REM that can lag a beat behind a just-started container, and init_db.py
REM below connects from the Windows host -- without this wait it can hit
REM a transient WinError 1225 (connection refused) right after startup.
echo [install] waiting for the Windows-host localhost:%MYSQL_PORT% forward to come up...
set /a WAIT_FORWARD_SECONDS=0
:wait_localhost_forward
set "FORWARD_OK=False"
for /f %%R in ('powershell -NoProfile -Command "(Test-NetConnection -ComputerName 127.0.0.1 -Port %MYSQL_PORT% -WarningAction SilentlyContinue).TcpTestSucceeded" 2^>nul') do set "FORWARD_OK=%%R"
if /i "!FORWARD_OK!"=="True" goto localhost_forward_ready
set /a WAIT_FORWARD_SECONDS+=2
if !WAIT_FORWARD_SECONDS! GEQ 30 (
    echo [install][WARN] localhost:%MYSQL_PORT% still not reachable from Windows after ~30s, continuing anyway
    goto localhost_forward_ready
)
timeout /t 2 /nobreak >nul
goto wait_localhost_forward
:localhost_forward_ready

REM ---- 4. Backend deps + DB schema/seed ----
echo [install] syncing backend dependencies ^(uv sync^)...
pushd "%BACKEND_DIR%"
call uv sync
if errorlevel 1 (
    echo [install] uv sync failed
    popd
    goto :end
)

echo [install] initializing database schema...
call uv run python init_db.py
if errorlevel 1 (
    echo [install] init_db.py failed
    popd
    goto :end
)

echo [install] initializing object storage bucket...
call uv run python init_storage.py
if errorlevel 1 (
    echo [install] init_storage.py failed
    popd
    goto :end
)
popd

echo [install] applying sql migrations / seed data...
for %%F in ("%BACKEND_DIR%\sql\*.sql") do (
    echo [install] applying %%~nxF
    %DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" exec -T mysql mysql --default-character-set=utf8mb4 -u!MYSQL_USER! -p!MYSQL_PASSWORD! !MYSQL_DATABASE! < "%%F"
    if errorlevel 1 (
        echo [install] failed applying %%~nxF
        goto :end
    )
)

REM ---- 5. Frontend deps ----
REM web/ is the only frontend these scripts manage day-to-day (app/front is
REM legacy-maintenance-only, see the 2026-07-19 frontend consolidation doc
REM under docs/); it is never
REM installed or pre-built here -- maintain it manually if ever needed.
echo [install] installing frontend dependencies ^(pnpm install, web/^)...
pushd "%WEB_DIR%"
call pnpm install
if errorlevel 1 (
    echo [install] pnpm install failed
    popd
    goto :end
)
popd

REM ---- 6. Pre-build docker images used by server.bat ----
echo [install] pre-building docker images for server.bat ^(backend/celery/init-db^)...
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" build backend celery-worker backend-init-db

echo [install] === setup complete. You can now run test.bat / run.bat / server.bat ===
echo [install] note: server.bat needs to be run as Administrator ^(it opens LAN-facing ports via netsh^).

:end
endlocal
pause
