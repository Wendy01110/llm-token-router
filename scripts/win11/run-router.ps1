param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $AppRoot

$Python = Join-Path $AppRoot ".venv\Scripts\python.exe"
$ConfigPath = Join-Path $AppRoot "config.yaml"
$DbPath = Join-Path $AppRoot "data\token_router.sqlite3"
$ReportsDir = Join-Path $AppRoot "reports"

if (-not (Test-Path $Python)) {
    throw "Missing project venv: $Python. Create it with: py -3.13 -m venv .venv"
}

if (-not (Test-Path $ConfigPath)) {
    throw "Missing config.yaml. Copy config.example.yaml to config.yaml, then edit .env."
}

New-Item -ItemType Directory -Force -Path `
    (Join-Path $AppRoot "data"), `
    (Join-Path $AppRoot "logs"), `
    $ReportsDir | Out-Null

$env:TOKEN_ROUTER_CONFIG = $ConfigPath
$env:TOKEN_ROUTER_DB = $DbPath
$env:TOKEN_ROUTER_REPORTS_DIR = $ReportsDir

& $Python -m uvicorn token_router.app.main:app --host $HostAddress --port $Port
