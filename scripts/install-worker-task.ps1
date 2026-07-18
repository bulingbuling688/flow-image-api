param(
    [string]$TaskName = "FlowImageApiWorker",
    [string]$RunScript = "D:\workspace\github\bulingbuling688\flow-image-api\scripts\run-worker.ps1"
)

$ErrorActionPreference = "Stop"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunScript`""
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
