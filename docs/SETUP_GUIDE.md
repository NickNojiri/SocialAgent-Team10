# 🛠️ SpotBot Setup Guide (from GitHub to a live bot)

*Everything from `git clone` to a spot card in your Discord, on Windows or
Mac/Linux. Every step has the "how do I know it worked?" answer, and the
troubleshooting table at the bottom covers the real snags people hit.*

**What you're setting up:** four local processes — the Discord bot, the catalog
app (:8010), the recommendation service (:8003), and Ollama (the local AI).
One script starts the first three.

**You'll need:** ~30 minutes, ~10 GB of disk (AI models), and a computer that
stays on while the bot should be online.

---

## Step 0 — Install the base tools (one time)

| Tool | Get it from | Check it works |
|---|---|---|
| **Python 3.11+** | [python.org/downloads](https://www.python.org/downloads/) — tick **"Add to PATH"** on Windows | `python --version` |
| **Git** | [git-scm.com](https://git-scm.com/downloads) | `git --version` |
| **Ollama** (local AI) | [ollama.com/download](https://ollama.com/download) | `ollama --version` |
| **ffmpeg** (audio, optional but recommended) | Windows: `winget install --id Gyan.FFmpeg -e` · Mac: `brew install ffmpeg` · Linux: `sudo apt install ffmpeg` | `ffmpeg -version` **in a new terminal** |

> 💡 After installing anything, **open a fresh terminal** — PATH changes don't
> reach terminals that were already open.

## Step 1 — Get the code

⚠️ **Work in a folder you own** — `Documents` or a `Projects` folder. Never
`C:\WINDOWS\system32` (where new terminals sometimes start): cloning there fails
with *Permission denied*.

```powershell
cd $HOME\Documents          # Mac/Linux: cd ~
git clone https://github.com/NickNojiri/SocialAgent-Team10.git
cd SocialAgent-Team10
```

✅ *Worked when:* your prompt ends with `SocialAgent-Team10>`.

## Step 2 — Install the Python pieces

**Windows (PowerShell):**
```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
pip install faster-whisper truststore
python -m playwright install chromium
```

> If PowerShell refuses to run the script (*"running scripts is disabled"*), run
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once,
> then retry.

**Mac/Linux:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt faster-whisper truststore
python -m playwright install chromium
```

✅ *Worked when:* your prompt shows a `(.venv)` prefix and
`pip show pytest` prints package info.

## Step 3 — Download the AI models

Open a **second terminal** and leave it running the whole time:
```powershell
ollama serve
```
*(If it says the address is already in use — good, Ollama's tray app is already
running. Move on.)*

Back in your first terminal:
```powershell
ollama pull llama3.1:8b
ollama pull llama3.2:1b
```
These are big (~5 GB total) — give them a few minutes.

✅ *Worked when:* `ollama list` shows both models, and http://localhost:11434 in
a browser says **"Ollama is running."**

## Step 4 — Create your Discord bot (~3 minutes)

1. [discord.com/developers/applications](https://discord.com/developers/applications) →
   **New Application** → name it (e.g. *SpotBot*) → **Create**
2. Sidebar **Bot** → **Reset Token** → **Copy**. Treat this like a password —
   never paste it in chats or commit it.
3. Same page, under **Privileged Gateway Intents**: turn **ON
   “Message Content Intent”** → Save. *(Without this the bot can't see pasted links.)*
4. Sidebar **OAuth2 → URL Generator**:
   - Scopes: ✅ `bot` ✅ `applications.commands`
   - Bot permissions: ✅ Send Messages · Embed Links · Add Reactions ·
     Create Public Threads · **Manage Events**
5. Copy the generated URL at the bottom → open it in your browser → choose your
   server → **Authorize**.

✅ *Worked when:* the bot appears (offline) in your server's member list.

## Step 5 — Configure

```powershell
Copy-Item .env.example .env      # Mac/Linux: cp .env.example .env
notepad .env                     # Mac/Linux: nano .env
```

Set the one required line (real token, no quotes, no spaces):
```
DISCORD_TOKEN=your_real_token_here
```

Recommended extras:
```
SPOT_QUORUM=3            # votes needed before "lock it in" (use 1 while testing solo)
OLLAMA_MODEL=llama3.2:3b # ~2-3x faster captures on CPU (run: ollama pull llama3.2:3b)
WHISPER_MODEL=tiny       # faster audio transcription
```
More knobs (all optional): `TRANSCRIBE_ENABLED=0` (skip audio, fastest),
`SETTLE_TIMEOUT_MS=4000`, `OCR_ENABLED=1` (read on-screen text; needs
`pip install rapidocr-onnxruntime`), `CAPTURE_BUDGET_S`, `BOT_TZ`.

## Step 6 — Launch 🚀

**Windows:** `.\scripts\run_local.ps1` *(add `-InsecureSsl` only if your network
intercepts TLS and the bot can't reach discord.com)*

**Mac/Linux** (three terminals, venv active in each — or use `docker compose up --build`):
```bash
uvicorn src.ingestion.serving.admin:app --port 8010
uvicorn src.ingestion.serving.app:app --port 8003
python app/bot.py
```

✅ *Worked when the bot window shows:*
```
=== Bot online: YourBot#1234 ===
[slash] Synced 7 slash commands
```

## Step 7 — The victory lap

1. In your server, paste an Instagram reel link into any channel
2. Watch: ⏳ → "🔎 Reading that reel…" → **a spot card** (~20–45s)
3. Tap 👍. With `SPOT_QUORUM=1`, tap **📅 Lock it in** → check the server's
   **Events** tab
4. Browse http://localhost:8010 (manage) and http://localhost:8010/share (share page)

You're live. Hand your members [USER_GUIDE.md](USER_GUIDE.md).

---

## Troubleshooting (real errors, real fixes)

| Symptom | Fix |
|---|---|
| `git clone` → *Permission denied* | You're in a system folder. `cd $HOME\Documents` first. |
| `.\scripts\setup.ps1` *not recognized* | You're not inside the repo folder — `cd` into it (prompt must end `SocialAgent-Team10>`). |
| *running scripts is disabled* | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, retry. |
| `ModuleNotFoundError: No module named 'audioop'` | Python 3.13 removed it. `pip install audioop-lts`. |
| Warning: *DISCORD_TOKEN is not set* | `.env` still has the placeholder text — paste your real token after `DISCORD_TOKEN=`. |
| `ollama` timeouts / model pull hangs | The server isn't running: `ollama serve` in its own terminal (or the tray app). Verify at http://localhost:11434. |
| `ffmpeg` not found but you installed it | Open a **new** terminal; PATH updates don't reach old ones. |
| Bot online but ⚠️ *"couldn't reach the catalog service"* | The :8010 window didn't start or crashed — check its window for the error. |
| Repeating `⏳ LLM not ready (getaddrinfo failed)` on old versions | Harmless legacy warning; fixed in current code (`git pull`). |
| Slash commands not appearing in Discord | Discord caches them — wait a few minutes (up to an hour), or kick the bot and re-invite. |
| Captures very slow / CPU pegged | Use the speed knobs in Step 5 — that's local AI working. See README "capture speed". |
| First capture after a restart is slowest | Normal — models load into RAM, then stay warm ~5 minutes. |

**Sanity check any time:** `pytest -k "not live" -q` from the repo root (venv
active) — all tests should pass. If they don't, that output is exactly what to
share when asking for help.
