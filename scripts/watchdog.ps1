<#
.SYNOPSIS
  Optional watchdog: checks /health every -IntervalSeconds; after -MaxFailures consecutive failures it restarts
  the server with run.ps1 -Stop / -Background and waits for /ready. Logs to logs\watchdog.log.
#>
param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [int]$IntervalSeconds = 15,
    [int]$MaxFailures = 3,
    [int]$ReadyTimeoutSeconds = 600
)
$Root = Split-Path $PSScriptRoot -Parent
$Run = Join-Path $Root "run.ps1"
$LogFile = Join-Path $Root "logs\watchdog.log"
New-Item -ItemType Directory -Force (Join-Path $Root "logs") | Out-Null

function Write-Log($msg) {
    $line = "$((Get-Date).ToString('s')) $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

$failures = 0
Write-Log "watchdog started for $BaseUrl"
while ($true) {
    try {
        Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 10 | Out-Null
        $failures = 0
    } catch {
        $failures++
        Write-Log "health check failed ($failures/$MaxFailures): $($_.Exception.Message)"
    }
    if ($failures -ge $MaxFailures) {
        Write-Log "restarting server"
        & $Run -Stop | Out-Null
        & $Run -Background | Out-Null
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            try {
                if ((Invoke-WebRequest "$BaseUrl/ready" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) {
                    $ready = $true; break
                }
            } catch { }
        }
        Write-Log $(if ($ready) { "server ready after restart" } else { "server NOT ready after restart" })
        $failures = 0
    }
    Start-Sleep -Seconds $IntervalSeconds
}
