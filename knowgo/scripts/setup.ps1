$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $demoRoot 'knowgo_travel_backend'
$frontendRoot = Join-Path $demoRoot 'frontend'

Push-Location $backendRoot
try {
    & uv sync --locked --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
} finally { Pop-Location }

Push-Location $frontendRoot
try {
    & npm.cmd ci --legacy-peer-deps --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
} finally { Pop-Location }

& (Join-Path $backendRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'configure_local.py')
if ($LASTEXITCODE -ne 0) { throw 'Local configuration failed.' }
Write-Host 'Dependencies installed. Start with .\start-demo.ps1.'
