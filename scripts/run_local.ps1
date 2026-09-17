#requires -Version 5.1
<#
Start the reel-capture stack locally: the admin app (ingest + catalog +
transcription), the recommend service (for /plan), and the Discord bot.

    .\scripts\run_local.ps1                # normal networks
    .\scripts\run_local.ps1 -InsecureSsl   # if your network intercepts TLS and
                                           # the bot can't reach discord.com

Run .\scripts\setup.ps1 once first. Ollama must be running. Ctrl+C stops the bot;
the admin + recommend windows stay open (close them to stop).
#>
param([switch]$InsecureSsl)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = ".\.venv\Scripts\python.exe"

# Every catalog call is signed with SPOTBOT_SIGNING_KEY (see .env.example). If a
# .env predates that, add a key now so the three processes below share one.
if (Test-Path ".env") { & $py scripts/ensure_signing_key.py }

# Load .env (DISCORD_TOKEN, SPOTBOT_SIGNING_KEY, etc.)
if (Test-Path ".env") {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            Set-Item -Path "env:$($matches[1])" -Value ($matches[2].Trim('"').Trim())
        }
    }
}
if (-not $env:DISCORD_TOKEN -or $env:DISCORD_TOKEN -like "*your-discord-bot-token*") {
    Write-Warning "DISCORD_TOKEN is not set in .env - the bot won't start. Edit .env first."
    return
}

# Host-run service URLs (override the Docker-oriented values from .env)
$env:PYTHONUTF8    = "1"
$env:OLLAMA_URL    = "http://localhost:11434"
$env:INGEST_URL    = "http://localhost:8010"
$env:ADMIN_URL     = "http://localhost:8010"
$env:RECOMMEND_URL = "http://localhost:8003"
if ($InsecureSsl) { $env:BOT_INSECURE_SSL = "1" }

if (-not $env:SPOTBOT_SIGNING_KEY) {
    Write-Warning "SPOTBOT_SIGNING_KEY is not set - catalog calls for a real server will be refused (503). Run: .\.venv\Scripts\python.exe scripts\ensure_signing_key.py"
}

# --host 127.0.0.1 explicitly: the legacy single-tenant catalog ("" guild, the
# local web UI) is unauthenticated by design and must not be reachable off-host.
Write-Host "Starting admin     -> http://localhost:8010" -ForegroundColor Cyan
Start-Process -FilePath $py -ArgumentList "-m","uvicorn","src.ingestion.serving.admin:app","--host","127.0.0.1","--port","8010" -WindowStyle Minimized
Write-Host "Starting recommend -> http://localhost:8003" -ForegroundColor Cyan
Start-Process -FilePath $py -ArgumentList "-m","uvicorn","src.ingestion.serving.app:app","--host","127.0.0.1","--port","8003" -WindowStyle Minimized

Start-Sleep -Seconds 6
Write-Host "`nStarting the Discord bot (Ctrl+C to stop)...`n" -ForegroundColor Green
& $py app/bot.py
