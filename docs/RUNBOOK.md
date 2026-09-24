# RUNBOOK — operating SpotBot

Rewritten 2026-09-15 to match what actually runs. Topology and ports:
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md) §1. First-time install:
[`docs/SETUP_GUIDE.md`](SETUP_GUIDE.md) or [`docs/RUN_AT_WORK.md`](RUN_AT_WORK.md).

## Start everything (Windows)

```powershell
.\scripts\run_local.ps1                # admin :8010 + recommend :8003 + the bot
.\scripts\run_local.ps1 -InsecureSsl   # networks that intercept TLS (campus/corporate)
```

Ollama must already be running (`ollama serve`, or the installed service). `Ctrl+C`
stops the bot; the two uvicorn windows stay up until you close them.

## Start by hand (any OS)

Three processes, three terminals, from the repo root with the venv active:

```bash
uvicorn src.ingestion.serving.admin:app --port 8010    # capture + catalog + web pages
uvicorn src.ingestion.serving.app:app --port 8003      # /recommend + /plan
python app/bot.py                                      # the Discord bot
```

Set `INGEST_URL=http://localhost:8010` and `RECOMMEND_URL=http://localhost:8003` for
the bot when running on the host (`run_local.ps1` does this; `.env.example` carries the
Docker-oriented defaults).

## Docker

`docker compose up --build` runs **only** the bot and the recommend service. The admin
app stays on the host (it drives Chromium and Whisper) and the containers reach it and
Ollama through `host.docker.internal`. Binding the admin app to anything other than
`127.0.0.1` for that purpose is a security decision — see `THREAT_MODEL.md` T8.

The containers read `SPOTBOT_SIGNING_KEY` from `.env`; the host-run admin app must get
the **same** value (for a systemd unit: `EnvironmentFile=` pointing at that `.env`).

## Check it is healthy — no Discord needed

```bash
python -m pytest -k "not live" -q                       # offline suite, ~2 min
python -m src.ingestion.eval --offline --split test     # extractor scorecard
curl http://localhost:8003/health                       # recommend up
curl http://localhost:8010/api/stats                    # admin up, catalog counts
```

Open `http://localhost:8010/dash` for the live operations page (captures, services,
per-guild counts).

## Environment variables that change behaviour

| Variable | Effect |
|---|---|
| `TRANSCRIBE_ENABLED=0` | skip Whisper — biggest speed win on CPU-only machines |
| `WHISPER_MODEL=tiny\|base\|small` | speed vs. venue-name accuracy (default `base`) |
| `OCR_ENABLED=1` | read on-screen text from the cover image (needs `rapidocr-onnxruntime`) |
| `CAPTURE_BUDGET_S` | hard per-link budget for post-fetch stages (default 180) |
| `SETTLE_TIMEOUT_MS` | how long the browser waits for the page (default 8000) |
| `EMBED_MODEL` | must match what the catalog was written with (default `mxbai-embed-large`) |
| `SPOTBOT_SIGNING_KEY` | **required** — signs every catalog call; the same value must reach the admin app, the recommend service and the bot. `python scripts/ensure_signing_key.py` creates it (setup and `run_local.ps1` do this) |
| `IG_USERNAME` / `IG_PASSWORD` | enable the authenticated fetch path (burner account only) |
| `BOT_INSECURE_SSL=1` | skip TLS verification on intercepting proxies |
| `SPOT_QUORUM` | votes before "Lock it in" appears (default 3) |

Full list: `README.md` → Configuration.

## Secrets — where they live, and rotating the signing key

Threat model T6. One leaked `SPOTBOT_SIGNING_KEY` lets whoever holds it read, change
or wipe **any** server's catalog, so it gets the most care.

**Where secrets live, and nowhere else:**

| Secret | Lives in | Notes |
|---|---|---|
| `SPOTBOT_SIGNING_KEY` | `.env` | Same value for the admin app, recommend service and bot |
| `DISCORD_TOKEN` | `.env` | Reset in the Discord developer portal if leaked |
| `IG_USERNAME` / `IG_PASSWORD` | `.env` | Burner account only; change the password on Instagram if leaked |
| Instagram session cookie | `data/ig_session.json` | Delete the file to force a fresh login |
| Old `.env` copies from rotation | `data/key-backups/` | Delete once you're sure you won't roll back |

Rules: never in the repo, never in a log line, never in a test fixture, never pasted
into Codex, Claude or any chat. `.env`, every `.env.*` copy and all of `data/` are
gitignored — only `.env.example`, which holds no values, is tracked. Check with
`git status` before every commit. On Windows, `.env` is protected only by your user
profile's folder permissions; don't keep the repo in a shared or synced folder.

**When to rotate the signing key:** you think it leaked (pasted somewhere, committed,
on a lost laptop), someone who had `.env` leaves the team, or you're about to deploy to
staging for the first time with a key that's lived on laptops.

**How to rotate:**

1. Preview — changes nothing:
   ```bash
   python scripts/rotate_signing_key.py
   ```
2. Rotate:
   ```bash
   python scripts/rotate_signing_key.py --confirm
   ```
   It backs up `.env` to `data/key-backups/`, writes a new key, and prints what to do
   next. It never prints either key.
3. Restart **all three**, admin app and recommend service first, then the bot. Until the
   bot restarts, every call it makes is refused (403), because it's still signing with
   the old key. With `run_local.ps1`: stop it and start it again.
4. Tell your servers their share links broke. Every `/share` link ever issued was
   signed with the old key and now shows an error; running `/share` again makes a new one.
5. Once everything works, delete the backup in `data/key-backups/`. It holds the old key.

**Undo** (only before the new key is in use anywhere): copy the backup back over `.env`
and restart all three.

**What rotation does not do:** it can't kill one leaked token while keeping the rest —
it kills all of them. Per-token expiry and revocation is Track D's #30 (Authentication &
session hardening), built on this same key.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every command or card says *"couldn't reach the catalog"*; admin log shows `403` or `503` | `SPOTBOT_SIGNING_KEY` missing (503) or different between processes (403) | `python scripts/ensure_signing_key.py`, then restart all three so they load the same `.env` |
| A `/share` link says *"no longer valid"* | the signing key was changed | run `/share` again for a fresh link |
| Bot online, cards say *"couldn't reach the catalog service"* | admin app not running, or the bot's `INGEST_URL` points at Docker's `host.docker.internal` while running on the host | start `:8010`; set `INGEST_URL=http://localhost:8010` |
| `/plan` and `/events` always empty, admin log shows `dimension` errors | recommend service embedding with a different model than the catalog | same `EMBED_MODEL` for both; re-embed from `data/inspirations.jsonl` if the model changed |
| Capture works but no 📝 description | Ollama down, or `llama3.1:8b` not pulled | `ollama serve`; `ollama pull llama3.1:8b` (optional feature) |
| Every capture "unreadable" | Instagram login wall on this network, or Chromium not installed | `python -m playwright install chromium`; try a hotspot; the `/embed/` fallback runs automatically |
| First capture takes ~40 s | Whisper model download (~145 MB) on first use | one-time |
| `UnicodeDecodeError` reading `labels.jsonl` | fixed 2026-09-15 (`c659c88c`) — pull `main` | — |
| Nominatim / geocoding "unresolved" | geocoding is off in the admin capture path by design; the CLI path needs a non-intercepting network | expected |
| `git push` fails with a certificate error | TLS-intercepting network | `git -c http.sslVerify=false push` |
