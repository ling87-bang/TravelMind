$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path $PSScriptRoot '.runtime'
foreach ($service in @('frontend', 'backend')) {
    $pidPath = Join-Path $runtimeRoot "$service.pid"
    if (Test-Path -LiteralPath $pidPath) {
        $servicePid = [int](Get-Content -LiteralPath $pidPath)
        $serviceProcess = Get-Process -Id $servicePid -ErrorAction SilentlyContinue
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $servicePid" -ErrorAction SilentlyContinue
        $owned = $processInfo -and $processInfo.CommandLine -and $processInfo.CommandLine.Contains($PSScriptRoot)
        if ($owned -and $serviceProcess -and $serviceProcess.ProcessName -in @('node', 'python', 'pythonw')) {
            Stop-Process -Id $servicePid
        }
        Remove-Item -LiteralPath $pidPath
    }
}
Push-Location $PSScriptRoot
try { & docker compose stop } finally { Pop-Location }
Write-Host 'Demo services stopped. Database volumes preserved.'
