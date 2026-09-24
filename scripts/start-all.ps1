param(
    [ValidateRange(1,65535)][int]$Port = 8000,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $projectPython = Join-Path $projectRoot '.venv-imported\Scripts\python.exe'
}

if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Ambiente .venv ausente. Consulte a seção "Rodar no Windows" do README.'
}

# ComfyUI local (opcional): CUTCLIPS_COMFYUI_DIR aponta para a pasta ComfyUI_windows_portable.
$comfyDir = $env:CUTCLIPS_COMFYUI_DIR
$envFile = Join-Path $projectRoot '.env'
if (-not $comfyDir -and (Test-Path -LiteralPath $envFile)) {
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*CUTCLIPS_COMFYUI_DIR\s*=' } | Select-Object -Last 1
    if ($line) { $comfyDir = ($line -split '=', 2)[1].Trim().Trim('"') }
}
if ($comfyDir) {
    $comfyPython = Join-Path $comfyDir 'python_embeded\python.exe'
    $comfyMain = Join-Path $comfyDir 'ComfyUI\main.py'
    $comfyRunning = $false
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:8188/system_stats' -TimeoutSec 1 | Out-Null; $comfyRunning = $true } catch {}
    if ($comfyRunning) {
        Write-Host 'ComfyUI já está em execução.'
    } elseif ((Test-Path -LiteralPath $comfyPython) -and (Test-Path -LiteralPath $comfyMain)) {
        $comfy = Start-Process $comfyPython -WindowStyle Hidden -PassThru -WorkingDirectory $comfyDir `
            -RedirectStandardOutput (Join-Path $comfyDir 'comfyui.log') -RedirectStandardError (Join-Path $comfyDir 'comfyui.err.log') `
            -ArgumentList @('-s', $comfyMain, '--windows-standalone-build', '--listen', '127.0.0.1', '--port', '8188')
        Write-Host "ComfyUI iniciando em segundo plano (PID $($comfy.Id)); os modelos carregam na primeira geração."
    } else {
        Write-Warning "CUTCLIPS_COMFYUI_DIR não contém um ComfyUI portátil válido: $comfyDir"
    }
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

Write-Host "CutClips pronto em $url"
Write-Host "API PID: $($api.Id) | Worker PID: $($worker.Id)"
if (-not $NoBrowser) { Start-Process $url }
