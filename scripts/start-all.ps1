param(
    [ValidateRange(1,65535)][int]$Port = 8000,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Ambiente .venv ausente. Consulte a seção "Rodar no Windows" do README.'
}

$api = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript,
    '-Mode', 'api', '-Port', $Port
)
$worker = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript,
    '-Mode', 'worker'
)

$url = "http://127.0.0.1:$Port/"
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri ($url + 'api/health') -TimeoutSec 1
        if ($health.ok) { $ready = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 300
}
if (-not $ready) {
    Stop-Process -Id $api.Id,$worker.Id -ErrorAction SilentlyContinue
    throw "O servidor não iniciou em $url. Verifique se a porta já está em uso."
}

Write-Host "ClipForge pronto em $url"
Write-Host "API PID: $($api.Id) | Worker PID: $($worker.Id)"
if (-not $NoBrowser) { Start-Process $url }
