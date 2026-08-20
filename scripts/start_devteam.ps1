param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appUrl = "http://127.0.0.1:8000"
$healthUrl = "$appUrl/api/v1/health"

function Test-DevTeamHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

try {
    Set-Location $projectRoot

    Write-Host ""
    Write-Host "  DevTeam Agent" -ForegroundColor Cyan
    Write-Host "  Multi-Agent Software Delivery Workbench" -ForegroundColor DarkGray
    Write-Host ""

    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "Python virtual environment was not found: $pythonPath"
    }

    Write-Host "[1/3] Checking runtime configuration..." -ForegroundColor Gray
    & $pythonPath -c "from backend.app.core.config import Settings; s=Settings.from_env(); print(f'      Provider: {s.llm_provider} / Model: {s.llm_model}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Configuration check failed. Verify the .env file."
    }

    Write-Host "[2/3] Checking service status..." -ForegroundColor Gray
    if (Test-DevTeamHealth) {
        Write-Host "      DevTeam Agent is already running." -ForegroundColor Yellow
        if (-not $Check) {
            Start-Process $appUrl
        }
        exit 0
    }

    if ($Check) {
        Write-Host "[3/3] Launcher check passed." -ForegroundColor Green
        exit 0
    }

    Write-Host "[3/3] Starting DevTeam Agent..." -ForegroundColor Gray
    Write-Host "      URL: $appUrl" -ForegroundColor DarkGray
    Write-Host "      Press Ctrl+C in this window to stop." -ForegroundColor DarkGray
    Write-Host ""

    $browserJob = Start-Job -ScriptBlock {
        param($TargetUrl, $TargetHealthUrl)
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri $TargetHealthUrl -TimeoutSec 2
                if ($health.status -eq "ok") {
                    Start-Process $TargetUrl
                    return
                }
            }
            catch {
                Start-Sleep -Milliseconds 500
            }
        }
    } -ArgumentList $appUrl, $healthUrl

    try {
        & $pythonPath -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
        if ($LASTEXITCODE -ne 0) {
            throw "The server exited with code $LASTEXITCODE."
        }
    }
    finally {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
catch {
    Write-Host ""
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
