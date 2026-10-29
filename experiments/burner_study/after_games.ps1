# Run GPU jobs only when no game is running: a game takes the 5700 XT's memory, Ollama then spills
# onto the integrated GPU and its runner crashes (and the game stutters). Waits until no game
# process has been seen for 3 minutes, then runs the dev scorecard with each model.
$ErrorActionPreference = "Continue"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
$games = "VALORANT-Win64-Shipping", "League of Legends", "cs2", "FortniteClient-Win64-Shipping"
$log = "data\experiments\queue\after_games.log"
function Note($m) { "[{0:MM-dd HH:mm:ss}] $m" -f (Get-Date) | Tee-Object -FilePath $log -Append }
Note "waiting for games to close"
$quiet = 0
while ($quiet -lt 3) {
    if (Get-Process -Name $games -ErrorAction SilentlyContinue) { $quiet = 0 } else { $quiet++ }
    Start-Sleep 60
}
Note "no game for 3 min: starting the dev scorecards"
$env:PYTHONUTF8 = "1"; $env:OLLAMA_URL = "http://localhost:11434"
foreach ($m in "llama3.2:latest", "llama3.1:8b") {
    $tag = $m -replace '[:.]', ''
    & .\.venv\Scripts\python.exe -m src.ingestion.eval --split dev --model $m *> "data\experiments\queue\eval_dev_$tag.log"
    $errs = @(Select-String -Path "data\experiments\queue\eval_dev_$tag.log" -Pattern 'transport error').Count
    Note "$m done ($errs Ollama errors; a run with errors fell back to rules for those reels)"
}
Note "all done"
