#requires -Version 5.1
<#
Start SpotBot's Instagram webhook (ADR-0007) and a public tunnel to it.

    .\scripts\run_local.ps1        # first, in another window: admin :8010 (+ recommend, bot)
    .\scripts\run_instagram.ps1    # then this: webhook :8011 + a cloudflared quick tunnel

The tunnel exposes ONLY the webhook app on :8011, never the admin app (THREAT_MODEL T8/T10).
A quick tunnel gets a new https://<random>.trycloudflare.com address each run, so paste the
callback URL this prints into the Meta dashboard again after every restart. For a fixed
address, run on the staging box instead (docs/RUNBOOK.md).

Needs in .env: IG_APP_SECRET (Meta dashboard → Instagram → API setup with Instagram login →
"Instagram app secret"). IG_WEBHOOK_VERIFY_TOKEN is created here if it's missing.
IG_ACCESS_TOKEN is optional (SpotBot replies "got it"). Then check: python scripts/check_instagram.py
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = ".\.venv\Scripts\python.exe"
$logs = "data\local_run"; New-Item -ItemType Directory -Force $logs | Out-Null

if (-not (Test-Path ".env")) { Write-Warning "No .env yet - copy .env.example to .env first."; return }
# A verify token is just a shared random string; make one so there's nothing to invent.
if (-not (Select-String -Path .env -Pattern '^\s*IG_WEBHOOK_VERIFY_TOKEN\s*=\s*\S' -Quiet)) {
    $tok = & $py -c "import secrets; print(secrets.token_urlsafe(24))"
    $raw = Get-Content .env -Raw -Encoding utf8
    $lead = if ($raw -and -not $raw.EndsWith("`n")) { "`r`n" } else { "" }   # don't glue onto the last line
    Add-Content -Path .env -Value "${lead}IG_WEBHOOK_VERIFY_TOKEN=$tok" -Encoding utf8
    Write-Host "Added IG_WEBHOOK_VERIFY_TOKEN to .env (type the same value into the Meta dashboard)." -ForegroundColor Yellow
}
Get-Content .env -Encoding utf8 | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { Set-Item -Path "env:$($matches[1])" -Value ($matches[2].Trim('"').Trim()) }
}
$env:PYTHONUTF8 = "1"
if (-not $env:ADMIN_URL -or $env:ADMIN_URL -like "*host.docker.internal*") { $env:ADMIN_URL = "http://127.0.0.1:8010" }
if (-not $env:IG_APP_SECRET) { Write-Warning "IG_APP_SECRET is not set in .env - Meta's deliveries will be refused (503). Add it, then rerun." }
if (-not (Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Warning "The admin app isn't running on :8010 - reels will arrive but can't be captured. Start .\scripts\run_local.ps1 first."
}

Write-Host "Starting the Instagram webhook -> http://127.0.0.1:8011" -ForegroundColor Cyan
$hook = Start-Process -FilePath $py -ArgumentList "-m","uvicorn","src.ingestion.serving.ig_webhook_app:app","--host","127.0.0.1","--port","8011" `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs\ig_webhook.out" -RedirectStandardError "$logs\ig_webhook.err"

$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cf) {
    Write-Warning "cloudflared isn't installed, so Meta can't reach this PC yet. Install it once with:"
    Write-Host "    winget install --id Cloudflare.cloudflared" -ForegroundColor White
    Write-Host "then rerun this script. (Webhook app is running; pid $($hook.Id).)"
    return
}
Remove-Item "$logs\tunnel.err" -ErrorAction SilentlyContinue
$tunnel = Start-Process -FilePath $cf.Source -ArgumentList "tunnel","--no-autoupdate","--url","http://127.0.0.1:8011" `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs\tunnel.out" -RedirectStandardError "$logs\tunnel.err"
$public = $null
foreach ($i in 1..30) {
    Start-Sleep 2
    $m = Select-String -Path "$logs\tunnel.err" -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { $public = $m.Matches[0].Value; break }
}
"webhook=$($hook.Id) tunnel=$($tunnel.Id)" | Set-Content "$logs\ig_pids.txt" -Encoding utf8
if (-not $public) {
    Write-Warning "The tunnel didn't come up in 60 s. See $logs\tunnel.err (a network that intercepts TLS can block it; try a hotspot)."
    return
}
$callback = "$public/webhooks/instagram"
try { Set-Clipboard -Value $callback } catch {}
Write-Host ""
Write-Host "Callback URL (copied to the clipboard):" -ForegroundColor Green
Write-Host "    $callback"
Write-Host "Verify token: the IG_WEBHOOK_VERIFY_TOKEN value in .env"
Write-Host "Meta dashboard -> your app -> Instagram -> API setup with Instagram login -> 'Configure webhooks':"
Write-Host "    paste both, click Verify and save, then subscribe to the 'messages' field."
Write-Host "Check everything:  $py scripts\check_instagram.py --public $public"
Write-Host "Stop:  Stop-Process -Id $($hook.Id),$($tunnel.Id)"
