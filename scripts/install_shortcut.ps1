#requires -Version 5.1
<#
Put a "SpotBot Control" shortcut on your desktop (and in the Start menu).

    powershell -ExecutionPolicy Bypass -File .\scripts\install_shortcut.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\install_shortcut.ps1 -NoTunnel

Double-click it to start SpotBot and open its control window. Run .\scripts\setup.ps1
once first. For a link phones can open from any network, install cloudflared once:
    winget install Cloudflare.cloudflared
#>
param([switch]$NoTunnel)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { throw "No virtual environment yet. Run .\scripts\setup.ps1 first." }

$arguments = '"' + (Join-Path $root "scripts\spotbot_control.py") + '"'
if ($NoTunnel) { $arguments += " --no-tunnel" }
$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"))
foreach ($dir in $places) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "SpotBot Control.lnk"))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root "scripts\spotbot.ico")
    $lnk.Description = "Start SpotBot and open its control window"
    $lnk.Save()
    Write-Host "Shortcut: $($lnk.FullName)" -ForegroundColor Green
}
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Warning "cloudflared isn't installed, so phones can only use SpotBot on this computer. Install it: winget install Cloudflare.cloudflared"
}
