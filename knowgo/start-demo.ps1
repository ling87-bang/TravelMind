param([switch]$Install, [switch]$Production)
$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$backendRoot = Join-Path $demoRoot 'knowgo_travel_backend'
$frontendRoot = Join-Path $demoRoot 'frontend'
$runtimeRoot = Join-Path $demoRoot '.runtime'
$pythonExe = Join-Path $backendRoot '.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:NEXT_TELEMETRY_DISABLED = '1'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

if ($Install) { & (Join-Path $demoRoot 'scripts\setup.ps1') }
if (!(Test-Path -LiteralPath $pythonExe) -or !(Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\next\dist\bin\next'))) {
    throw 'Dependencies are missing. Run .\start-demo.ps1 -Install first.'
}

Push-Location $demoRoot
try {
    & docker compose up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'Database startup failed. Check Docker Desktop.' }
} finally { Pop-Location }

function Test-Http([string]$Url) {
    try { return (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 }
    catch { return $false }
}

if (!(Test-Http 'http://127.0.0.1:8000/api/v1/health/ready')) {
    $backendProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'app.run') -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'backend.out.log') -RedirectStandardError (Join-Path $runtimeRoot 'backend.err.log')
    $backendProcess.Id | Set-Content (Join-Path $runtimeRoot 'backend.pid')
}

$backendReady = $false
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    if (Test-Http 'http://127.0.0.1:8000/api/v1/health/ready') { $backendReady = $true; break }
    if ($backendProcess -and $backendProcess.HasExited) { throw 'Backend exited. Inspect .runtime\backend.err.log.' }
    Start-Sleep -Seconds 2
}
if (!$backendReady) { throw 'Backend startup timed out. Inspect .runtime\backend.err.log.' }

if (!(Test-Http 'http://127.0.0.1:3000')) {
    $nodeExe = (Get-Command node.exe).Source
    $nextEntry = Join-Path $frontendRoot 'node_modules\next\dist\bin\next'
    $frontendMode = 'dev'
    if ($Production) {
        if (!(Test-Path -LiteralPath (Join-Path $frontendRoot '.next\BUILD_ID'))) {
            Push-Location $frontendRoot
            try {
                & $nodeExe $nextEntry build
                if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
            } finally { Pop-Location }
        }
        $frontendMode = 'start'
    }
    $frontendProcess = Start-Process -FilePath $nodeExe -ArgumentList @($nextEntry, $frontendMode, '--hostname', '127.0.0.1', '--port', '3000') -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'frontend.out.log') -RedirectStandardError (Join-Path $runtimeRoot 'frontend.err.log')
    $frontendProcess.Id | Set-Content (Join-Path $runtimeRoot 'frontend.pid')
}

$frontendReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (Test-Http 'http://127.0.0.1:3000') { $frontendReady = $true; break }
    if ($frontendProcess -and $frontendProcess.HasExited) { throw 'Frontend exited. Inspect .runtime\frontend.err.log.' }
    Start-Sleep -Seconds 2
}
if (!$frontendReady) { throw 'Frontend startup timed out. Inspect .runtime\frontend.err.log.' }
Write-Host 'Frontend: http://127.0.0.1:3000'
Write-Host 'API docs: http://127.0.0.1:8000/docs'
Write-Host 'Stop with .\stop-demo.ps1. Database data will be preserved.'
