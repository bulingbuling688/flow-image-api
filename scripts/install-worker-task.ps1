param(
    [string]$TaskName = "FlowImageApiWorker",
    [string]$Python = "D:\workspace\runtime\flow-image-api-venv\Scripts\python.exe",
    [string]$EnvFile = "D:\workspace\data\flow-image-api\worker.env",
    [string]$ProjectRoot = "D:\workspace\github\bulingbuling688\flow-image-api"
)

$ErrorActionPreference = "Stop"
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -m pip install --no-deps --editable $ProjectRoot
$installExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousPreference
if ($installExitCode -ne 0) {
    throw "Worker package installation failed with exit code $installExitCode"
}

$action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m flow_image_api.worker --env `"$EnvFile`"" `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Poll the public Flow Image API and execute jobs with gflow" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "task=$TaskName status=started"
