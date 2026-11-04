<#
.SYNOPSIS
  hr_ai one-command start for Windows (twin of start.sh, keep the two in sync). Detects the GPU, builds the matching
  image, downloads the model once, starts the API and the dashboards.
.EXAMPLE
  .\start.ps1                   # start (first run: image build + ~8 GB model download)
  .\start.ps1 -Cpu              # force CPU even when a GPU exists
  .\start.ps1 -DownloadModel    # ignore a local .\model and use the Hugging Face copy in a Docker volume
  .\start.ps1 -Port 8011 -GrafanaPort 3011   # when the default ports are busy
  .\start.ps1 stop              # also: status, logs
#>
param(
    [ValidateSet("up", "stop", "status", "logs")][string]$Action = "up",
    [switch]$Cpu,
    [switch]$DownloadModel,
    [string]$Port = $(if ($env:HR_AI_PORT) { $env:HR_AI_PORT } else { "8001" }),
    [string]$GrafanaPort = $(if ($env:HR_AI_GRAFANA_PORT) { $env:HR_AI_GRAFANA_PORT } else { "3001" })
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$EnvFile = ".env.docker"
$MinRamCpuMib = 9500  # bf16 weights ~8 GB; a Docker VM set to 10 GB reports about 9.7 GiB
$MinVramMb = 4500     # 4-bit model ~2.8 GB + ~2 GB transient while generating
$ReadyTimeout = 900

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "DIQQAT: $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "XATO: $m" -ForegroundColor Red; exit 1 }

function Invoke-Docker {
    # Stops the script on a failed docker command; PowerShell 5.1 does not do this for native tools.
    # "Continue": with "Stop", 5.1 turns any stderr line (docker writes progress there) into a fatal error.
    $ErrorActionPreference = "Continue"
    & docker @args
    if ($LASTEXITCODE -ne 0) { Die "docker $($args -join ' ') muvaffaqiyatsiz tugadi (kod $LASTEXITCODE)." }
}

function Invoke-Compose {
    Invoke-Docker compose --env-file $EnvFile -f docker-compose.yml -f "docker/compose.$script:Overlay.yml" @args
}

function Test-DockerUp {
    $ErrorActionPreference = "Continue"
    & docker info 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Test-ComposeVersion {
    $ErrorActionPreference = "Continue"
    $version = "$(& docker compose version --short 2>$null)".TrimStart("v")
    if ($version -notmatch "^(\d+)\.(\d+)") { Die "Docker Compose v2.24+ kerak: https://docs.docker.com/compose/install/" }
    if ([int]$Matches[1] -lt 2 -or ([int]$Matches[1] -eq 2 -and [int]$Matches[2] -lt 24)) {
        Die "Docker Compose $version eski, v2.24+ kerak. Docker Desktop'ni yangilang."
    }
}

function Initialize-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Die "Docker o'rnatilmagan: https://docs.docker.com/desktop/setup/install/windows-install/"
    }
    Test-ComposeVersion
    if (Test-DockerUp) { return }
    $exe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) { Die "Docker Desktop topilmadi. Uni ishga tushirib qayta urinib ko'ring." }
    Info "Docker Desktop ishga tushirilmoqda..."
    Start-Process $exe
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 3
        if (Test-DockerUp) { return }
    }
    Die "Docker Desktop ishga tushmadi."
}

function Get-Hardware {
    $hw = @{ Accel = "cpu"; Overlay = "cpu"; Index = "cpu"; Version = "2.11.0"; Bnb = 0; Device = "cpu"
             Dtype = "bfloat16"; Quant = "none"; Timeout = 600; Name = "CPU" }
    if ($Cpu) { return $hw }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        # Capture first: Select-Object -First in the pipeline stops nvidia-smi early and spoils $LASTEXITCODE.
        $lines = @(& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits)
        if ($LASTEXITCODE -eq 0 -and $lines.Count -gt 0) {
            $name, $vram = $lines[0] -split ",\s*"
            $text = (& nvidia-smi | Out-String)
            if ($text -notmatch "CUDA Version:\s*(\d+)\.(\d+)") { Die "nvidia-smi CUDA versiyasini ko'rsatmadi." }
            $num = [int]$Matches[1] * 100 + [int]$Matches[2]
            $cuda = "$($Matches[1]).$($Matches[2])"
            if ($num -ge 1208) { $hw.Index = "cu128"; $hw.Version = "2.11.0" }
            elseif ($num -ge 1206) { $hw.Index = "cu126"; $hw.Version = "2.11.0" }
            elseif ($num -ge 1108) { $hw.Index = "cu118"; $hw.Version = "2.7.1" }
            else { Die "NVIDIA drayveri juda eski (CUDA $cuda). Drayverni yangilang: https://www.nvidia.com/drivers" }
            $hw.Accel = $hw.Index; $hw.Overlay = "nvidia"; $hw.Bnb = 1
            $hw.Device = "auto"; $hw.Dtype = "auto"; $hw.Quant = "auto"; $hw.Timeout = 60
            $hw.Name = "NVIDIA $name, $vram MiB VRAM, CUDA $cuda"
            if ([int]$vram -lt $MinVramMb) {
                # The 4-bit model would run out of memory: split it between GPU and RAM instead (slow, but it works).
                Warn "VRAM $vram MiB kam ($MinVramMb MiB dan past): model GPU va RAM orasida bo'linadi, javoblar sekinroq bo'ladi."
                $hw.Bnb = 0; $hw.Quant = "none"; $hw.Timeout = 180
            }
            return $hw
        }
    }
    $other = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "AMD|Radeon|Intel.*Arc" } |
        Select-Object -First 1
    if ($other) {
        Warn "$($other.Name): Docker Windows'da faqat NVIDIA GPU'ni konteynerga bera oladi, model CPU'da ishlaydi (sekin)."
    } else {
        Warn "Mos GPU topilmadi: model CPU'da ishlaydi (sekin, bir so'rov bir necha daqiqa)."
    }
    return $hw
}

function Test-Memory($hw) {
    $ErrorActionPreference = "Continue"
    $mib = [math]::Floor([int64](& docker info --format "{{.MemTotal}}") / 1MB)
    if ($mib -ge $MinRamCpuMib) { return }
    if ($hw.Overlay -eq "cpu") {
        Die "Docker'ga $mib MiB RAM berilgan, CPU rejimi uchun kamida 10 GB kerak. %UserProfile%\.wslconfig faylida [wsl2] ostiga memory=12GB yozing, keyin 'wsl --shutdown' bajarib Docker Desktop'ni qayta oching."
    }
    if ($hw.Quant -eq "none") { Warn "Docker'ga $mib MiB RAM berilgan: model GPU va RAM orasida bo'linadi, 10 GB tavsiya etiladi." }
}

function Test-Ports {
    $ErrorActionPreference = "Continue"
    $own = "$(& docker ps --filter label=com.docker.compose.project=hr_ai --format '{{.Ports}}')"
    foreach ($p in @($Port, $GrafanaPort)) {
        if ($own -like "*:$p->*") { continue }
        if (Get-NetTCPConnection -State Listen -LocalPort ([int]$p) -ErrorAction SilentlyContinue) {
            Die "$p-port band. Boshqa port bilan: start.bat -Port 8011 -GrafanaPort 3011"
        }
    }
}

function Write-DockerEnv($hw) {
    $source = "hr_ai_models"; $mode = "rw"; $readonly = 0
    if (-not $DownloadModel -and (Test-Path "model\config.json")) {
        $source = "./model"; $mode = "ro"; $readonly = 1
        Info "Mavjud .\model ishlatiladi (faqat o'qish uchun), qayta yuklanmaydi."
    }
    $lines = @(
        "# Generated by start.ps1 for this machine. Do not commit.",
        "HR_AI_OVERLAY=$($hw.Overlay)", "HR_AI_ACCEL=$($hw.Accel)", "TORCH_INDEX=$($hw.Index)",
        "TORCH_VERSION=$($hw.Version)", "INSTALL_BNB=$($hw.Bnb)", "MODEL_DEVICE=$($hw.Device)",
        "MODEL_DTYPE=$($hw.Dtype)", "MODEL_QUANTIZATION=$($hw.Quant)", "LLM_TIMEOUT_SECONDS=$($hw.Timeout)",
        "HR_AI_PORT=$Port", "HR_AI_GRAFANA_PORT=$GrafanaPort", "HR_AI_MODEL_SOURCE=$source",
        "HR_AI_MODEL_MODE=$mode", "HR_AI_MODEL_READONLY=$readonly"
    )
    # ASCII, not PowerShell 5.1's default: a BOM would corrupt the first variable for docker compose.
    [IO.File]::WriteAllLines((Join-Path $PSScriptRoot $EnvFile), [string[]]$lines, (New-Object Text.ASCIIEncoding))
}

function Test-GpuInContainer($hw) {
    if ($hw.Overlay -ne "nvidia") { return }
    $ErrorActionPreference = "Continue"
    & docker run --rm --gpus all --entrypoint python "hr_ai:$($hw.Accel)" -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)"
    if ($LASTEXITCODE -ne 0) {
        Die "GPU konteynerga ko'rinmayapti. Docker Desktop WSL2 rejimida bo'lsin va NVIDIA drayverini yangilang. GPU'siz davom etish: .\start.ps1 -Cpu"
    }
}

function Wait-Ready {
    Info "Model yuklanmoqda, /ready kutilmoqda..."
    $deadline = (Get-Date).AddSeconds($ReadyTimeout)
    $composeArgs = @("compose", "--env-file", $EnvFile, "-f", "docker-compose.yml", "-f", "docker/compose.$script:Overlay.yml")
    while ((Get-Date) -lt $deadline) {
        # Crash loop, usually out of memory: do not wait the full timeout.
        $ErrorActionPreference = "Continue"
        $cid = "$(& docker @composeArgs ps -q api 2>$null)"
        if ($cid -and [int]"$(& docker inspect -f '{{.RestartCount}}' $cid 2>$null)" -ge 2) { break }
        $ErrorActionPreference = "Stop"
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:$Port/ready" -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { return }
        } catch {
            try {
                if ((Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3).modelStatus -eq "failed") { break }
            } catch { }
        }
        Start-Sleep 3
    }
    $ErrorActionPreference = "Continue"
    & docker compose --env-file $EnvFile -f docker-compose.yml -f "docker/compose.$script:Overlay.yml" logs --tail 40 api
    Die "API tayyor bo'lmadi. Yuqoridagi logga qarang."
}

function Show-Report($hw) {
    $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($hw.Overlay -ne "cpu" -and "$($health.device)" -notlike "cuda*") {
        Warn "GPU kutilgan edi, lekin model '$($health.device)' da ishlayapti."
    }
    $ErrorActionPreference = "Continue"
    $running = "$(& docker compose --env-file $EnvFile -f docker-compose.yml -f "docker/compose.$script:Overlay.yml" ps --status running --services 2>$null)"
    foreach ($service in "prometheus", "grafana") {
        if ($running -notlike "*$service*") { Warn "$service ishlamayapti: docker compose logs $service" }
    }
    Write-Host ""
    Info "Tayyor. Qurilma: $($hw.Name) ($($health.device), $($health.dtype))"
    Write-Host "    Dashboard + prompt tahriri: http://127.0.0.1:$Port/"
    Write-Host "    Monitoring (Grafana):       http://127.0.0.1:$GrafanaPort/"
    Write-Host "    API hujjatlari (Swagger):   http://127.0.0.1:$Port/docs"
    Write-Host "    To'xtatish: .\start.ps1 stop | Holat: .\start.ps1 status | Log: .\start.ps1 logs"
    Start-Process "http://127.0.0.1:$Port/"
}

Initialize-Docker
if ($Action -ne "up") {
    if (-not (Test-Path $EnvFile)) { Die "$EnvFile topilmadi. Avval .\start.ps1 ni ishga tushiring." }
    $script:Overlay = ((Get-Content $EnvFile | Where-Object { $_ -like "HR_AI_OVERLAY=*" }) -split "=")[1]
    $Port = ((Get-Content $EnvFile | Where-Object { $_ -like "HR_AI_PORT=*" }) -split "=")[1]
    switch ($Action) {
        "stop" { Invoke-Compose stop }
        "status" {
            Invoke-Compose ps
            try { Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3 | ConvertTo-Json -Compress } catch { Write-Host "API javob bermayapti." }
        }
        "logs" { Invoke-Compose logs -f --tail 100 api }
    }
    exit 0
}

$hw = Get-Hardware
$script:Overlay = $hw.Overlay
Info "Aniqlangan qurilma: $($hw.Name) (torch $($hw.Version), $($hw.Index))"
Test-Memory $hw
Test-Ports
Write-DockerEnv $hw
Info "Image qurilmoqda (birinchi marta 10-40 daqiqa, internetga bog'liq)..."
$ErrorActionPreference = "Continue"
& docker compose --env-file $EnvFile -f docker-compose.yml -f "docker/compose.$($hw.Overlay).yml" build api
if ($LASTEXITCODE -ne 0) {
    & docker image inspect "hr_ai:$($hw.Accel)" *> $null
    if ($LASTEXITCODE -ne 0) { Die "Image qurilmadi: internetni tekshirib qayta urinib ko'ring." }
    Warn "Image yangilanmadi (internet?), mavjud image ishlatiladi."
}
$ErrorActionPreference = "Stop"
Test-GpuInContainer $hw
Info "Model tekshirilmoqda (kerak bo'lsa Hugging Face'dan yuklanadi)..."
Invoke-Compose run --rm --no-deps model-init
Info "Konteynerlar ko'tarilmoqda..."
Invoke-Compose up -d
Wait-Ready
Show-Report $hw
