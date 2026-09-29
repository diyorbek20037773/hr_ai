<#
.SYNOPSIS
  Poll /health, /ready, /metrics and print a one-line status; append to logs\monitor.log.
  Exits 1 after -MaxFailures consecutive failures, 0 when -DurationSeconds elapses.
.EXAMPLE
  .\scripts\monitor.ps1 -IntervalSeconds 5 -DurationSeconds 120
#>
param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [int]$IntervalSeconds = 5,
    [int]$MaxFailures = 3,
    [int]$DurationSeconds = 0   # 0 = run forever
)
$Root = Split-Path $PSScriptRoot -Parent
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$LogFile = Join-Path $LogDir "monitor.log"

function Get-HistogramQuantile([string]$metrics, [string]$name, [double]$q) {
    $buckets = @()
    foreach ($line in $metrics -split "`n") {
        if ($line -match "^${name}_bucket\{.*le=`"([^`"]+)`".*\}\s+([0-9.eE+-]+)") {
            $le = if ($Matches[1] -eq "+Inf") { [double]::PositiveInfinity } else { [double]$Matches[1] }
            $buckets += [pscustomobject]@{ le = $le; count = [double]$Matches[2] }
        }
    }
    if ($buckets.Count -eq 0) { return $null }
    # sum across label sets per bucket boundary
    $grouped = $buckets | Group-Object le | ForEach-Object {
        [pscustomobject]@{ le = [double]$_.Name; count = ($_.Group | Measure-Object count -Sum).Sum }
    } | Sort-Object le
    $total = ($grouped | Select-Object -Last 1).count
    if (-not $total) { return $null }
    foreach ($b in $grouped) { if ($b.count -ge $q * $total) { return $b.le } }
    return $null
}

function Get-Gauge([string]$metrics, [string]$name) {
    foreach ($line in $metrics -split "`n") {
        if ($line -match "^$name\s+([0-9.eE+-]+)") { return [double]$Matches[1] }
    }
    return $null
}

$failures = 0
$checks = 0
$start = Get-Date
while ($true) {
    $ts = (Get-Date).ToString("s")
    try {
        $health = Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 10
        $readyCode = 0
        try { $readyCode = (Invoke-WebRequest "$BaseUrl/ready" -UseBasicParsing -TimeoutSec 10).StatusCode }
        catch { $readyCode = [int]$_.Exception.Response.StatusCode }
        $metrics = (Invoke-WebRequest "$BaseUrl/metrics" -UseBasicParsing -TimeoutSec 10).Content
        if ($metrics -is [byte[]]) { $metrics = [Text.Encoding]::UTF8.GetString($metrics) }
        $p95 = Get-HistogramQuantile $metrics "hr_ai_llm_inference_duration_seconds" 0.95
        $queue = Get-Gauge $metrics "hr_ai_llm_queue_depth"
        $gpu = Get-Gauge $metrics "hr_ai_gpu_memory_used_bytes"
        $gpuMb = if ($gpu) { [math]::Round($gpu / 1MB) } else { 0 }
        $p95s = if ($p95) { "${p95}s" } else { "n/a" }
        $state = if ($readyCode -eq 200) { "UP" } else { "STARTING" }
        $line = "$ts $state model=$($health.modelLoaded) status=$($health.modelStatus) device=$($health.device) " +
                "ready=$readyCode llm_p95<=$p95s queue=$queue gpuMB=$gpuMb uptime=$($health.uptimeSeconds)s"
        $failures = 0
    } catch {
        $failures++
        $line = "$ts DOWN failures=$failures/$MaxFailures error=$($_.Exception.Message)"
    }
    $checks++
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    if ($failures -ge $MaxFailures) {
        Write-Host "MONITOR: $failures consecutive failures - exiting with code 1"
        exit 1
    }
    if ($DurationSeconds -gt 0 -and ((Get-Date) - $start).TotalSeconds -ge $DurationSeconds) {
        Write-Host "MONITOR: finished $checks checks in $DurationSeconds s, 0 open failures"
        exit 0
    }
    Start-Sleep -Seconds $IntervalSeconds
}
