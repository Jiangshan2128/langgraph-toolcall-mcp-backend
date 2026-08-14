#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Start the Banana Todo List Backend server.
.DESCRIPTION
    Starts uvicorn with the FastAPI app.  Activates .venv if present.
.PARAMETER Port
    Port to listen on (default: 8000).
.PARAMETER Host
    Host to bind to (default: 0.0.0.0).
.PARAMETER NoReload
    Disable hot-reload (useful for production-like runs).
#>
param(
    [int]    $Port    = 8000,
    [string] $Bind    = "0.0.0.0",
    [switch] $NoReload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# ── Activate virtual environment if it exists ──────────────────────
$Venv = Join-Path $ProjectRoot ".venv"
if (Test-Path $Venv) {
    $Activate = Join-Path $Venv "Scripts\Activate.ps1"
    if (Test-Path $Activate) {
        . $Activate
        Write-Host "✅ venv activated: $Venv"
    }
}

# ── Start uvicorn ─────────────────────────────────────────────────
Set-Location $ProjectRoot
Write-Host "🚀 Starting Banana Todo List Backend on http://${Host}:${Port}"
Write-Host "   Root: $ProjectRoot"
Write-Host ""

$reload = if (-not $NoReload) { "--reload" } else { $null }
$args = @(
    "app.main:fastApi",
    "--host", $Bind,
    "--port", $Port
)
if ($reload) { $args += $reload }

try {
    uvicorn @args
} catch {
    Write-Error "Failed to start uvicorn: $_"
    exit 1
}
