@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - run.bat
REM Quick startup once the code is stable: assumes dependencies
REM are already installed (see install.bat / test.bat). Starts
REM backend + the web/ workbench directly in tagged terminal
REM windows, skipping dependency reinstall. app/front (legacy
REM Studio) is not started -- see the 2026-07-19 frontend consolidation
REM doc under docs/.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "BACKEND_DIR=%APP_DIR%\backend"
set "WEB_DIR=%ROOT%web"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

set "BACKEND_TITLE=shotcat-backend-run"
set "WEB_TITLE=shotcat-web-run"

echo [run] === shotcat quick startup ===

if not exist "%COMPOSE_ENV%" (
    echo [run] %COMPOSE_ENV% not found. Run install.bat first.
    goto :end
)

REM Docker Desktop is not allowed here; infra runs via Docker Engine inside
REM WSL2 (set up by install.bat). Translate this project's Windows path to
REM the equivalent WSL path so docker compose resolves everything correctly.
for /f "delims=" %%W in ('wsl -- wslpath -a "%ROOT:~0,-1%"') do set "WSL_ROOT=%%W"
set "WSL_COMPOSE_DIR=%WSL_ROOT%/app/deploy/compose"
set "WSL_COMPOSE_ENV=%WSL_COMPOSE_DIR%/.env"
set "WSL_COMPOSE_FILE=%WSL_COMPOSE_DIR%/docker-compose.yml"
REM Dev override: publishes mysql/redis/rustfs on 127.0.0.1 for the native
REM dev flow (base compose publishes no host ports at all; see the file).
set "WSL_COMPOSE_DEV_FILE=%WSL_COMPOSE_DIR%/docker-compose.dev.yml"
set "DOCKER=wsl -- docker"

REM Avoid port clashes with a full docker stack started by server.bat.
for /f %%I in ('%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" ps -q backend celery-worker 2^>nul') do (
    echo [run] found running server.bat containers on the same ports, stopping them first...
    %DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" stop backend celery-worker
    goto conflict_handled
)
:conflict_handled

echo [run] ensuring infra containers are up ^(mysql/redis/rustfs^)...
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" -f "%WSL_COMPOSE_DEV_FILE%" up -d mysql redis rustfs
if errorlevel 1 (
    echo [run] failed to start infra containers, run install.bat first
    goto :end
)

echo [run] waiting for mysql to become healthy...
set /a WAIT_SECONDS=0
:wait_mysql
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" ps mysql | findstr /i "healthy" >nul 2>&1
if not errorlevel 1 goto mysql_ready
set /a WAIT_SECONDS+=2
if !WAIT_SECONDS! GEQ 60 (
    echo [run] mysql did not become healthy within 60s, aborting
    goto :end
)
timeout /t 2 /nobreak >nul
goto wait_mysql
:mysql_ready

echo [run] starting backend ^(uv run uvicorn --reload^) in window "%BACKEND_TITLE%"...
start "%BACKEND_TITLE%" cmd /k "cd /d "%BACKEND_DIR%" && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

echo [run] starting web/ workbench dev server ^(pnpm dev^) in window "%WEB_TITLE%"...
start "%WEB_TITLE%" cmd /k "cd /d "%WEB_DIR%" && pnpm dev"

echo [run] === services started ===
echo [run] backend: http://localhost:8000/docs
echo [run] workbench ^(web/^): http://localhost:5273 ^(opens automatically^)
echo [run] app/front ^(legacy Studio, not started by default^): cd app\front ^&^& pnpm dev

:end
endlocal
