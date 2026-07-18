param(
    [string]$EnvFile = "D:\workspace\data\flow-image-api\worker.env",
    [string]$Python = "D:\workspace\runtime\flow-image-api-venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $Python -m flow_image_api.worker --env $EnvFile
exit $LASTEXITCODE
