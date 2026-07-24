@echo off
REM Start AI Note Backend
REM Usage: scripts\start.bat [port]

set PORT=%1
if "%PORT%"=="" set PORT=8000

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Port %PORT%
if %ERRORLEVEL% NEQ 0 (
    pause
)
