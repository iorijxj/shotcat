@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - test.bat
REM Startup for testing after code changes: reinstalls backend/
REM frontend dependencies, regenerates the OpenAPI client, then
REM starts backend + frontend in tagged terminal windows.
REM Use this whenever there is a recompile/dependency step needed.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "BACKEND_DIR=%APP_DIR%\backend"
set "FRONT_DIR=%APP_DIR%\front"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

set "BACKEND_TITLE=shotcat-backend-test"
set "FRONT_TITLE=shotcat-front-test"

echo [test] === shotcat test startup ^(full reinstall + recompile^) ===

if not exist "%COMPOSE_ENV%" (
    echo [test] %COMPOSE_ENV% not found. Run install.bat first.
    goto :end
)

REM Docker Desktop is not allowed here; infra runs via Docker Engine inside
REM WSL2 (set up by install.bat). Translate this project's Windows path to
REM the equivalent WSL path so docker compose resolves everything correctly.
for /f "delims=" %%W in ('wsl -- wslpath -a "%ROOT:~0,-1%"') do set "WSL_ROOT=%%W"
set "WSL_COMPOSE_DIR=%WSL_ROOT%/app/deploy/compose"
set "WSL_COMPOSE_ENV=%WSL_COMPOSE_DIR%/.env"
set "WSL_COMPOSE_FILE=%WSL_COMPOSE_DIR%/docker-compose.yml"
set "DOCKER=wsl -- docker"

REM Avoid port clashes with a full docker stack started by server.bat.
for /f %%I in ('%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" ps -q backend front celery-worker 2^>nul') do (
    echo [test] found running server.bat containers on the same ports, stopping them first...
    %DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" stop backend front celery-worker
    goto conflict_handled
)
:conflict_handled

echo [test] ensuring infra containers are up ^(mysql/redis/rustfs^)...
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" up -d mysql redis rustfs
if errorlevel 1 (
    echo [test] failed to start infra containers, run install.bat first
    goto :end
)

echo [test] waiting for mysql to become healthy...
set /a WAIT_SECONDS=0
:wait_mysql
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" ps mysql | findstr /i "healthy" >nul 2>&1
if not errorlevel 1 goto mysql_ready
set /a WAIT_SECONDS+=2
if !WAIT_SECONDS! GEQ 60 (
    echo [test] mysql did not become healthy within 60s, aborting
    goto :end
)
timeout /t 2 /nobreak >nul
goto wait_mysql
:mysql_ready

echo [test] syncing backend dependencies ^(uv sync^)...
pushd "%BACKEND_DIR%"
call uv sync
if errorlevel 1 (
    echo [test] uv sync failed
    popd
    goto :end
)
popd

echo [test] installing frontend dependencies ^(pnpm install^)...
pushd "%FRONT_DIR%"
call pnpm install
if errorlevel 1 (
    echo [test] pnpm install failed
    popd
    goto :end
)
popd

echo [test] starting backend ^(uv run uvicorn --reload^) in window "%BACKEND_TITLE%"...
start "%BACKEND_TITLE%" cmd /k "cd /d "%BACKEND_DIR%" && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

echo [test] waiting for backend to become reachable on port 8000...
set /a WAIT_SECONDS=0
:wait_backend
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8000/openapi.json 2>nul | findstr "200" >nul 2>&1
if not errorlevel 1 goto backend_ready
set /a WAIT_SECONDS+=2
if !WAIT_SECONDS! GEQ 60 (
    echo [test] backend did not become reachable within 60s, skipping openapi:update
    goto skip_openapi
)
timeout /t 2 /nobreak >nul
goto wait_backend
:backend_ready

echo [test] regenerating OpenAPI client ^(pnpm run openapi:update^)...
pushd "%FRONT_DIR%"
call pnpm run openapi:update
popd

:skip_openapi
echo [test] starting frontend dev server ^(pnpm dev^) in window "%FRONT_TITLE%"...
start "%FRONT_TITLE%" cmd /k "cd /d "%FRONT_DIR%" && pnpm dev"

echo [test] === test environment started ===
echo [test] backend: http://localhost:8000/docs
echo [test] frontend: http://localhost:7788 ^(opens automatically^)

:end
endlocal
