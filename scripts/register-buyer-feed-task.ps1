# Register Windows Task Scheduler job for Planto buyer feed (every 2 hours).
#
# Usage (from elixir-dashboard repo root):
#   .\scripts\register-buyer-feed-task.ps1
#   .\scripts\register-buyer-feed-task.ps1 -Unregister

param(
    [string]$Time = "00:00",
    [int]$EveryHours = 2,
    [string]$TaskName = "PlantoBuyerFeed",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$feedDir = Join-Path $repoRoot "scripts\buyer-feed"
$mainPy = Join-Path $feedDir "__main__.py"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $mainPy)) {
    throw "Missing $mainPy — copy scripts/buyer-feed from Planto buyer package"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

$action = New-ScheduledTaskAction `
    -Execute $python.Source `
    -Argument "`"$mainPy`" --work-dir `"$repoRoot`"" `
    -WorkingDirectory $feedDir

$trigger = New-ScheduledTaskTrigger -Once -At $Time `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Planto buyer feed: Direct + AppMetrica Reporting + Supabase -> data/planto-*.csv/json every $EveryHours h" | Out-Null

Write-Host "Registered: $TaskName (every $EveryHours h from $Time)" -ForegroundColor Green
