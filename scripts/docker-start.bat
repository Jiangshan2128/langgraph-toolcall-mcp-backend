@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Banana Todo List Backend - Docker start script
REM  Usage: double-click, or run "docker-start.bat" from a terminal
REM
REM  Why this script exists:
REM   - The container MUST receive .env vars at CREATION time (--env-file)
REM   - Docker Desktop GUI "Run" does NOT read .env -> bare container
REM     cannot reach Supabase / LLM
REM   - docker stop/start keeps env, but if you delete the container you
REM     MUST recreate it with --env-file. This script does exactly that.
REM ============================================================

cd /d "%~dp0"
set "PROJECT_DIR=%cd%"
set "CONTAINER=ainote-backend"
set "PORT=8000"

echo.
echo [Banana Todo List] Docker start script
echo [Banana Todo List] Project dir: %PROJECT_DIR%
echo.

REM ---- 1. Check .env exists ----
if not exist "%PROJECT_DIR%\.env" (
    echo [ERROR] .env not found in %PROJECT_DIR%
    echo         Copy .env.example to .env and fill in your keys.
    pause
    exit /b 1
)
echo [OK] .env found

REM ---- 2. Check Docker is running ----
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not running. Start Docker Desktop first.
    pause
    exit /b 1
)
echo [OK] Docker running

REM ---- 3. Check image exists ----
docker image inspect ainote-backend:test >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Image ainote-backend:test not found.
    echo         Build it first: docker build -t ainote-backend:test .
    pause
    exit /b 1
)
echo [OK] Image found

REM ---- 4. Check port (best-effort; a real conflict surfaces as docker run error) ----
netstat -ano > "%TEMP%\_ainote_netstat.txt" 2>nul
findstr ":%PORT% " "%TEMP%\_ainote_netstat.txt" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port %PORT% may be in use. If it is the old %CONTAINER% container,
    echo        the script will stop it. If it is another program - e.g. a local
    echo        uvicorn - stop that program first, then retry.
)
del "%TEMP%\_ainote_netstat.txt" >nul 2>&1

REM ---- 5. Remove old container with same name (only if it exists) ----
docker ps -q --filter "name=^%CONTAINER%$" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Removing old container %CONTAINER% ...
    docker rm -f %CONTAINER% >nul 2>&1
)

REM ---- 6. Start container with .env ----
echo [START] Creating container %CONTAINER% with --env-file .env ...
docker run -d --name %CONTAINER% -p %PORT%:80 --env-file "%PROJECT_DIR%\.env" ainote-backend:test

if errorlevel 1 (
    echo [ERROR] Failed to start container.
    pause
    exit /b 1
)
echo [OK] Container started.

REM ---- 7. Wait for health (curl, Windows 10+ has it) ----
echo [WAIT] Waiting for service init (5-20s)...
set "READY=0"
for /l %%i in (1,1,20) do (
    curl -s -o nul -w "%%{http_code}" "http://localhost:%PORT%/health" 2>nul | findstr "200" >nul 2>&1
    if not errorlevel 1 (
        set "READY=1"
        goto :ready
    )
    timeout /t 2 /nobreak >nul
)
:ready
if "!READY!"=="1" (
    echo [OK] Service ready: http://localhost:%PORT%/health
) else (
    echo [WARN] Service not ready in time. Check logs: docker logs %CONTAINER%
)
echo.
echo ----------------------------------------------
echo  Container: %CONTAINER%
echo  URL:       http://localhost:%PORT%/
echo  Swagger:   http://localhost:%PORT%/docs
echo  LAN:       http://YOUR-LAN-IP:%PORT%
echo  Logs:      docker logs -f %CONTAINER%
echo  Stop:      docker stop %CONTAINER%   (env kept; start again to resume)
echo ----------------------------------------------
echo.
if /i "%~1"=="-nopause" goto :end
pause
:end
exit /b 0
