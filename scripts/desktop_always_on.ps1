#requires -Version 5.1
<#
Keep this desktop serving SpotBot around the clock, and reachable from Claude while you're away.

    powershell -ExecutionPolicy Bypass -File .\scripts\desktop_always_on.ps1           # set up
    powershell -ExecutionPolicy Bypass -File .\scripts\desktop_always_on.ps1 -Undo     # put it back

What it does (run it once, in a normal PowerShell window; it asks for admin rights itself):
  1. Power: plugged in, the PC never sleeps or hibernates. The screen may still turn off.
  2. SpotBot Control starts by itself every time you sign in (Task Scheduler, "SpotBot
     Control"), with no window; it restarts crashed parts on its own. Double-click the
     desktop shortcut any time to see its window.
  3. If the Claude CLI is installed, "claude remote-control" starts in this folder at sign-in
     ("SpotBot Claude Remote"), so this folder shows up in the Claude app on your phone and
     you can ask Claude to fix things here from school.

Windows still has to be signed in for 2 and 3 to run. After an update restart, turn on
Settings > Accounts > Sign-in options > "Use my sign-in info to automatically finish setting
up after an update", so it signs back in by itself.
#>
param([switch]$Undo)
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $argList = @("-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Undo) { $argList += "-Undo" }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    return
}

$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$control = Join-Path $root "scripts\spotbot_control.py"
$tasks = @("SpotBot Control", "SpotBot Claude Remote")

if ($Undo) {
    powercfg /change standby-timeout-ac 30
    powercfg /change hibernate-timeout-ac 0
    foreach ($t in $tasks) { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host "Done: the PC sleeps after 30 minutes again, and nothing starts at sign-in." -ForegroundColor Green
    return
}

if (-not (Test-Path $pyw)) { throw "No virtual environment yet. Run .\scripts\setup.ps1 first." }

# 1. Never sleep while plugged in (the display can still turn off to save power).
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Host "Power: this PC no longer sleeps or hibernates while plugged in." -ForegroundColor Green

# 2 and 3: start at sign-in, restart if they stop, never time out.
$user = "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$control`" --no-window" -WorkingDirectory $root
Register-ScheduledTask -TaskName $tasks[0] -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Starts SpotBot (catalog, plans, gatekeeper, tunnel, bot) at sign-in." -Force | Out-Null
Write-Host "Startup: SpotBot Control starts by itself at sign-in." -ForegroundColor Green

$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$($claude.Source)`" remote-control" -WorkingDirectory $root
    Register-ScheduledTask -TaskName $tasks[1] -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Lets you reach this SpotBot folder from the Claude app." -Force | Out-Null
    Write-Host "Remote: 'claude remote-control' starts in this folder at sign-in." -ForegroundColor Green
} else {
    Write-Warning "The Claude CLI isn't installed, so remote access wasn't set up. Install Claude Code, sign in once with 'claude', then run this script again."
}

# Start both now instead of waiting for the next sign-in.
foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
Write-Host "`nAll set. Leave the PC plugged in and signed in. Undo: .\scripts\desktop_always_on.ps1 -Undo"
