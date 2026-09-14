# Run the reel-capture stack on another machine (e.g. at work)

Everything lives on GitHub, so you just clone, set up once, and run. This runs the
**host** stack (Discord bot + ingest/catalog + transcription + `/plan`). It is
separate from the team's Docker `docker-compose.yml`.

## 0. Install these first (one time)
- **Python 3.11+** — https://www.python.org/downloads/ (tick "Add python.exe to PATH")
- **Ollama** — https://ollama.com/download (local models: catalog embeddings, optional summaries)
- **Git** — https://git-scm.com/download/win

## 1. Get the code
```powershell
git clone https://github.com/NickNojiri/SocialAgent-Team10.git
cd SocialAgent-Team10
```
`main` is the working branch — no checkout needed.

## 2. Set up (one command)
```powershell
.\scripts\setup.ps1
```
This makes a `.venv`, installs all deps (ingestion + `discord.py` + `faster-whisper`),
downloads the Playwright Chromium browser, pulls the Ollama models
(`mxbai-embed-large` required, `llama3.1:8b` optional), and creates `.env` from
`.env.example`.

## 2b. Check it works — no Discord token needed
```powershell
.\.venv\Scripts\python.exe -m pytest -k "not live" -q
.\.venv\Scripts\python.exe -m src.ingestion.eval --offline --split test
```
The first should pass. The second prints the extractor's honest held-out scorecard —
the numbers the team is working to move.

## 3. Add your Discord token
Open `.env` and set `DISCORD_TOKEN=...` (get it from the
[Discord Developer Portal](https://discord.com/developers/applications) → your app →
Bot). Also enable **Message Content Intent** there. **Never commit `.env`.**

## 4. Run it
```powershell
.\scripts\run_local.ps1
# if your work network intercepts TLS and the bot can't connect to discord.com:
.\scripts\run_local.ps1 -InsecureSsl
```
Then **DM the bot an Instagram reel** → you get a card with the venue, a 📝 quick
description from the audio (or "No info"), and a ▶ Watch-the-reel link. `/plan` and
the website (http://localhost:8010) also work.

## Notes
- **Ollama must be running** (the installer usually runs it as a service; otherwise
  `ollama serve`). First transcription downloads the Whisper model (~145 MB) once.
- The **catalog (`data/`) starts empty** — it's not in git. Capture some reels (or
  copy a `data/` folder over) to populate it.
- First reel capture takes **~20–40s** (the Whisper transcription step).
- **Mac/Linux:** the logic is identical; use `.venv/bin/python`, `source .venv/bin/activate`,
  and run the same `uvicorn ...` / `python app/bot.py` commands by hand (the `.ps1`
  scripts are Windows-only).
