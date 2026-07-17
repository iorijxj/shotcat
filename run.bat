@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM shotcat - run.bat
REM Quick startup once the code is stable: assumes dependencies
REM are already installed (see install.bat / test.bat). Starts
REM backend + frontend directly in tagged terminal windows,
REM skipping dependency reinstall and OpenAPI regeneration.
REM ============================================================

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%app"
set "BACKEND_DIR=%APP_DIR%\backend"
set "FRONT_DIR=%APP_DIR%\front"
set "COMPOSE_DIR=%APP_DIR%\deploy\compose"
set "COMPOSE_FILE=%COMPOSE_DIR%\docker-compose.yml"
set "COMPOSE_ENV=%COMPOSE_DIR%\.env"

set "BACKEND_TITLE=shotcat-backend-run"
set "FRONT_TITLE=shotcat-front-run"

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
set "DOCKER=wsl -- docker"

REM Avoid port clashes with a full docker stack started by server.bat.
for /f %%I in ('%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" ps -q backend front celery-worker 2^>nul') do (
    echo [run] found running server.bat containers on the same ports, stopping them first...
    %DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" stop backend front celery-worker
    goto conflict_handled
)
:conflict_handled

echo [run] ensuring infra containers are up ^(mysql/redis/rustfs^)...
%DOCKER% compose --env-file "%WSL_COMPOSE_ENV%" -f "%WSL_COMPOSE_FILE%" up -d mysql redis rustfs
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

echo [run] starting frontend dev server ^(pnpm dev^) in window "%FRONT_TITLE%"...
start "%FRONT_TITLE%" cmd /k "cd /d "%FRONT_DIR%" && pnpm dev"

echo [run] === services started ===
echo [run] backend: http://localhost:8000/docs
echo [run] frontend: http://localhost:7788 ^(opens automatically^)

:end
endlocal
