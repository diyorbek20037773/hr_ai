<#
.SYNOPSIS
  Start / stop the hr_ai API (uvicorn, exactly 1 worker, no reload).
.EXAMPLE
  .\run.ps1               # foreground
  .\run.ps1 -Background   # background, PID -> logs\server.pid
  .\run.ps1 -Stop         # stop the background server
#>
param(
    [switch]$Background,
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
$PidFile = Join-Path $LogDir "server.pid"
New-Item -ItemType Directory -Force $LogDir | Out-Null

if ($Stop) {
    if (Test-Path $PidFile) {
        $procId = [int](Get-Content $PidFile -Raw).Trim()
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $procId -Force
            Write-Host "Stopped hr_ai server (PID $procId)"
        } else {
            Write-Host "No running process with PID $procId"
        }
        Remove-Item $PidFile -Force
    } else {
        Write-Host "No PID file at $PidFile - server not started with -Background?"
    }
    exit 0
}

if (-not (Test-Path $Python)) { throw "Virtualenv python not found at $Python" }

# Load .env (KEY=VALUE lines) into this process environment if present.
$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"').Trim("'"), "Process")
        }
    }
}
if (-not $env:APP_HOST) { $env:APP_HOST = "127.0.0.1" }
if (-not $env:APP_PORT) { $env:APP_PORT = "8001" }
$env:PYTHONIOENCODING = "utf-8"

$UvArgs = @("-m", "uvicorn", "app.main:app", "--host", $env:APP_HOST, "--port", $env:APP_PORT,
            "--workers", "1", "--log-level", "info", "--no-access-log")

if ($Background) {
    if (Test-Path $PidFile) {
        $old = [int](Get-Content $PidFile -Raw).Trim()
        if (Get-Process -Id $old -ErrorAction SilentlyContinue) {
            Write-Host "hr_ai already running (PID $old) on http://$($env:APP_HOST):$($env:APP_PORT)"
            exit 0
        }
    }
    $proc = Start-Process -FilePath $Python -ArgumentList $UvArgs -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "server.out.log") `
        -RedirectStandardError (Join-Path $LogDir "server.err.log") -PassThru
    Set-Content -Path $PidFile -Value $proc.Id
    Write-Host "hr_ai started in background (PID $($proc.Id)) on http://$($env:APP_HOST):$($env:APP_PORT)"
    Write-Host "The model loads in the background; poll /ready until 200."
    exit 0
}

& $Python @UvArgs
