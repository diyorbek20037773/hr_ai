<#
.SYNOPSIS
  End-to-end smoke test against the RUNNING server: health, ready, the 10 core queries through /resolve with
  references\example_references.json as referenceData. Prints a PASS/FAIL table; exit code 0 only if all pass.
#>
param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [string]$ApiKey = $env:AI_SEARCH_API_KEY
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$refs = Get-Content (Join-Path $Root "references\example_references.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$headers = @{}
if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

# query -> expected searchModel fields (IDs from references\example_references.json)
$cases = @(
    @{ q = "Oliy ma'lumotli, 30 yoshdan katta ayollar";
       e = @{ educationId = 1; fromAge = 30; sexId = 2 } },
    @{ q = "Samarqand viloyatidagi 25-40 yoshli davlat xizmatchilari";
       e = @{ regionId = 8; fromAge = 25; toAge = 40; typeOfService = 1 } },
    @{ q = "Toshkentda ishlaydigan, 3 yildan ortiq staji bor iqtisodchilar";
       e = @{ regionId = 14; specialistIds = "1"; experienceYear = 3 } },
    @{ q = "Ilmiy darajasi bor va mukofotlangan xodimlar";
       e = @{ isAcademicDegree = $true; isAwarded = $true } },
    @{ q = "Олий маълумотли 30 ёшдан катта аёллар";
       e = @{ educationId = 1; fromAge = 30; sexId = 2 } },
    @{ q = "Женщины старше 30 лет с высшим образованием";
       e = @{ educationId = 1; fromAge = 30; sexId = 2 } },
    @{ q = "Familiyasi Karimov, ismi Ali";
       e = @{ lastName = "Karimov"; firstName = "Ali" } },
    @{ q = "Chilonzor tumanida yashaydigan, ingliz tilini biladigan, IELTS sertifikati bor yoshlar 35 yoshgacha";
       e = @{ districtId = 102; languageId = 3; languageCertificateId = 1; toAge = 35 } },
    @{ q = "bo'lim boshliqlari va direktorlar";
       e = @{ typeOfPosition = "1" } },
    @{ q = "yaxshi kadrlarni top";
       e = @{ } ; empty = $true }
)

$rows = @()
$allPass = $true

function Add-Row($name, $pass, $detail, $ms) {
    $script:rows += [pscustomobject]@{ Check = $name; Result = $(if ($pass) { "PASS" } else { "FAIL" }); ms = $ms; Detail = $detail }
    if (-not $pass) { $script:allPass = $false }
}

try {
    $h = Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 10
    Add-Row "GET /health" ($h.status -eq "ok") "modelLoaded=$($h.modelLoaded) device=$($h.device)" 0
} catch { Add-Row "GET /health" $false $_.Exception.Message 0 }
try {
    $r = Invoke-WebRequest "$BaseUrl/ready" -UseBasicParsing -TimeoutSec 10
    Add-Row "GET /ready" ($r.StatusCode -eq 200) "status=$($r.StatusCode)" 0
} catch { Add-Row "GET /ready" $false $_.Exception.Message 0 }

$base = @("inn","firstName","lastName","middleName","regionId","birthRegionId","districtId","sexId","nationId",
          "languageId","fromAge","toAge","educationId","fromYear","toYear","specialistId","specialistIds",
          "educationTypes","eduTypeId","degreeId","academicDegreeId","institutionInn","institutionId",
          "institutionName","institutionTypeId","institutionGroupId","typeOfPosition","spCategoryOfPositionId",
          "isAwarded","isReserved","languageCertificateId","isAcademicDegree","isAcademicTitle",
          "isGlobalEducation","term","pinfl","experienceYear","dateOfReceipt","typeOfService")

foreach ($c in $cases) {
    $body = @{ query = $c.q; referenceData = $refs } | ConvertTo-Json -Depth 10 -Compress
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        $res = Invoke-RestMethod "$BaseUrl/api/v1/ai-search/resolve" -Method Post -Headers $headers `
            -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 180
        $sw.Stop()
        $problems = @()
        if (-not $res.readyToSearch) { $problems += "readyToSearch=false" }
        foreach ($k in $c.e.Keys) {
            $got = $res.searchModel.$k
            $exp = $c.e[$k]
            $gotS = if ($got -is [array]) { ($got -join ",") } else { "$got" }
            if ($gotS -ne "$exp") { $problems += "$k=$gotS (want $exp)" }
        }
        # no invented filters: every other filter field must be absent
        foreach ($k in $base) {
            if (-not $c.e.ContainsKey($k) -and $null -ne $res.searchModel.$k) { $problems += "invented $k" }
        }
        $detail = if ($problems) { $problems -join "; " } else { ($res.searchModel | ConvertTo-Json -Compress) }
        Add-Row $c.q ($problems.Count -eq 0) $detail $sw.ElapsedMilliseconds
    } catch {
        $sw.Stop()
        Add-Row $c.q $false $_.Exception.Message $sw.ElapsedMilliseconds
    }
}

$rows | Format-Table -AutoSize -Wrap | Out-String -Width 220 | Write-Host
$passed = ($rows | Where-Object Result -eq "PASS").Count
Write-Host "SMOKE: $passed/$($rows.Count) passed"
if ($allPass) { exit 0 } else { exit 1 }
