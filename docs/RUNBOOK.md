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
| `IG_USERNAME` / `IG_PASSWORD` | enable the authenticated fetch path (burner account only) |
| `BOT_INSECURE_SSL=1` | skip TLS verification on intercepting proxies |
| `SPOT_QUORUM` | votes before "Lock it in" appears (default 3) |

Full list: `README.md` → Configuration.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bot online, cards say *"couldn't reach the catalog service"* | admin app not running, or the bot's `INGEST_URL` points at Docker's `host.docker.internal` while running on the host | start `:8010`; set `INGEST_URL=http://localhost:8010` |
| `/plan` and `/events` always empty, admin log shows `dimension` errors | recommend service embedding with a different model than the catalog | same `EMBED_MODEL` for both; re-embed from `data/inspirations.jsonl` if the model changed |
| Capture works but no 📝 description | Ollama down, or `llama3.1:8b` not pulled | `ollama serve`; `ollama pull llama3.1:8b` (optional feature) |
| Every capture "unreadable" | Instagram login wall on this network, or Chromium not installed | `python -m playwright install chromium`; try a hotspot; the `/embed/` fallback runs automatically |
| First capture takes ~40 s | Whisper model download (~145 MB) on first use | one-time |
| `UnicodeDecodeError` reading `labels.jsonl` | fixed 2026-09-15 (`c659c88c`) — pull `main` | — |
| Nominatim / geocoding "unresolved" | geocoding is off in the admin capture path by design; the CLI path needs a non-intercepting network | expected |
| `git push` fails with a certificate error | TLS-intercepting network | `git -c http.sslVerify=false push` |
