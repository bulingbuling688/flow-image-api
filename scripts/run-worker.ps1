param(
    [string]$EnvFile = "D:\workspace\data\flow-image-api\worker.env",
    [string]$Python = "D:\workspace\runtime\flow-image-api-venv\Scripts\python.exe",
    [string]$LogFile = "D:\workspace\state\flow-image-api\worker.log"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Split-Path -Parent $LogFile
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $projectRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"

& $Python -m flow_image_api.worker --env $EnvFile *>> $LogFile
exit $LASTEXITCODE
