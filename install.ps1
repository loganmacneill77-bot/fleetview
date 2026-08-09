# Register FleetView as a Scheduled Task so it starts at logon with no console
# window. A task, not a shortcut: a process launched from an agent shell or a
# terminal dies with that shell's process tree, which is a confusing way to
# lose your dashboard.
#
#   .\install.ps1              register (or re-register) and start
#   .\install.ps1 -Uninstall   remove
#
# Re-run this after a Python upgrade: the task stores an absolute pythonw path.

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$taskName = "FleetView"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask  -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed the $taskName task."
    } else {
        Write-Host "No $taskName task to remove."
    }
    Write-Host "Note: this does not stop a FleetView you started by hand."
    return
}

# pythonw.exe, not python.exe - pythonw has no console window at all
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python is not on PATH. Install Python 3.9+ first." }
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Could not find pythonw.exe next to $python" }

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "fleetview.py"
if (-not (Test-Path $script)) { throw "fleetview.py is not next to install.ps1" }

$action  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" `
                                   -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# no time limit, and don't stop it when the machine goes on battery
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
              -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
              -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "FleetView - Claude Code session dashboard" | Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Host "Registered and started '$taskName'."
Write-Host "  python : $pythonw"
Write-Host "  script : $script"

Start-Sleep -Seconds 3
try {
    Invoke-WebRequest -Uri "http://localhost:7936/data.json" -UseBasicParsing `
        -TimeoutSec 5 | Out-Null
    Write-Host "Serving on http://localhost:7936"
} catch {
    Write-Warning "Not answering yet on port 7936. Check fleetview.log in $here."
}

Write-Host ""
Write-Host "Restart it later with:"
Write-Host "  Stop-ScheduledTask -TaskName $taskName; Start-ScheduledTask -TaskName $taskName"
Write-Host ""
Write-Host "IMPORTANT: to stop FleetView by hand, kill it by PID."
Write-Host "'taskkill /IM python.exe' will take down every other Python service you run."
