#requires -Version 5.1
<#
One-time setup to RUN the SocialAgent reel-capture stack locally (e.g. on a work
machine). Run from the repo root:

    .\scripts\setup.ps1

Install these yourself first:
  - Python 3.11+   https://www.python.org/downloads/   (check "Add to PATH")
  - Ollama         https://ollama.com/download          (the local LLM runtime)
  - Git            https://git-scm.com/download/win

Then, to start everything:  .\scripts\run_local.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = ".\.venv\Scripts\python.exe"

Write-Host "`n[1/5] Python virtual env" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }

Write-Host "`n[2/5] Python dependencies (ingestion + bot + transcription)" -ForegroundColor Cyan
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-ingestion.txt
& $py -m pip install -r app/requirements.txt
& $py -m pip install faster-whisper truststore

Write-Host "`n[3/5] Playwright browser (Chromium)" -ForegroundColor Cyan
& $py -m playwright install chromium

Write-Host "`n[4/5] Ollama models" -ForegroundColor Cyan
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    ollama pull llama3.1:8b      # extraction + the quick-description summaries
    ollama pull llama3.2:1b      # embeddings for the catalog
} else {
    Write-Warning "Ollama not found on PATH. Install it from https://ollama.com/download, then run:"
    Write-Warning "    ollama pull llama3.1:8b ;  ollama pull llama3.2:1b"
}

Write-Host "`n[5/5] .env" -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Warning "Created .env - OPEN IT and set DISCORD_TOKEN before running the bot."
} else {
    Write-Host "  .env already exists - leaving it."
}

Write-Host "`nSetup complete. Start the stack with:  .\scripts\run_local.ps1" -ForegroundColor Green
