param(
    [ValidateSet('api','worker')][string]$Mode = 'api',
    [ValidateRange(1,65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

# Alguns lançadores isolados deixam um proxy sentinela em 127.0.0.1:9. Ele não
# é um proxy real e impede yt-dlp e clientes de IA de acessar a rede. Não altere
# proxies configurados pelo usuário para qualquer outro endereço.
foreach ($proxyName in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')) {
    $proxyValue = [Environment]::GetEnvironmentVariable($proxyName, 'Process')
    if ($proxyValue -match '^https?://(127[.]0[.]0[.]1|localhost):9/?$') {
        Remove-Item -LiteralPath "Env:$proxyName" -ErrorAction SilentlyContinue
    }
}
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $projectPython = Join-Path $projectRoot '.venv-imported\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Crie o ambiente .venv e instale requirements.txt antes de iniciar.'
}
$venvRoot = Split-Path -Parent (Split-Path -Parent $projectPython)
$localFFmpeg = Join-Path $venvRoot 'Lib\site-packages\static_ffmpeg\bin\win32'
if (Test-Path -LiteralPath (Join-Path $localFFmpeg 'ffmpeg.exe')) {
    $env:PATH = $localFFmpeg + [IO.Path]::PathSeparator + $env:PATH
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw 'FFmpeg e FFprobe precisam estar no PATH. Consulte o README.'
}
if ($Mode -eq 'api') {
    & $projectPython -P (Join-Path $PSScriptRoot 'launch.py') api $Port
} else {
    & $projectPython -P (Join-Path $PSScriptRoot 'launch.py') worker
}
exit $LASTEXITCODE
