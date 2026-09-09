# SpotBot — Full Stack

Paste an Instagram/TikTok reel in a group chat → it becomes a votable spot card →
votes reach quorum → the bot books a Discord Scheduled Event → the morning after,
it asks whether you actually went. Zero paid APIs, runs on a laptop, post text
never leaves the machine.

## Language & core

- **Python 3.12** (3.13-ready — `audioop-lts` backport for discord.py)
- **Pydantic 2** — strict schema validation at every pipeline boundary
- **pydantic-settings** — env-bound configuration

## Interface layer

- **discord.py 2.7** — the bot: slash commands, persistent `DynamicItem` button/view
  components (survive restarts), Discord **Scheduled Events**, message-content intent
  for link capture

## Services — FastAPI + Uvicorn, containerized

| Service | Port | Role |
|---|---|---|
| **admin / ingest** | 8010 | reel capture, catalog, votes, follow-ups; serves `/dash` (operator dashboard), `/` (admin), `/share` (public catalog page) |
| **recommend** | 8003 | `/recommend` (vibe search), `/plan` (group planning over the chat transcript) |

Server-rendered HTML + vanilla JS for the three web pages — no frontend framework.

## Ingestion / extraction pipeline

- **Playwright 1.60** (headless Chromium) — reel/post fetch, with a plain-HTTP
  embed-page fallback for login-walled pages
- **Extractor ladder:** JSON-LD / Open Graph tags → per-domain DOM selectors →
  in-house **slot-first regex parser** (`@handle`, `from X`, `"quoted name"`,
  first-person → poster, `at X` with container-demote) → **area-hashtag gazetteer**
  → **handle word-split** (bundled vocab, no dependency) → per-field **confidence
  scoring**
- **dateparser 1.4** — natural-language time phrases → absolute dates
- Optional, all degradable to "no signal":
  - **faster-whisper** — reel audio → transcript (needs system `ffmpeg`)
  - **rapidocr-onnxruntime** — burned-in on-screen text OCR
  - **instagrapi** — authenticated Instagram fetch (95–99% reliability path)

## AI / data

- **Ollama** (local inference)
  - `mxbai-embed-large` / `nomic-embed-text` — embeddings for the vector store
  - `llama3.2` — field extraction, wired as a **gap-filler only** (measured worse
    than the heuristics on CPU; kept for a future GPU / bigger model)
- **ChromaDB 1.5** — vector store, **one collection per Discord guild**
  (multi-tenant), HNSW index + SQLite full-text search
- **Nominatim (OpenStreetMap)** — geocoding · **Overpass API** — venue search
  (no Google/Foursquare, no paid resolver)
- **Golden-dataset eval harness** — `fixtures/labels.jsonl` (the labeled corpus)
  + `src/ingestion/eval.py` scorecard + CI accuracy floors in
  `test_extraction_labels.py`

## Storage

- **JSONL sink** — the source of truth for every ingestion result
- **ChromaDB sink** — vectors + metadata (best-effort, opt-in)
- **SQLite** — Chroma's metadata + FTS segments
- `channels.json` — per-channel bot config (mute / suggestions / tipped)

## Infra & ops

- **Docker + docker-compose** — bridge network, `host-gateway` so containers reach
  Ollama + the admin app on the host
- **systemd** user units — host-run admin app
- **GitHub Actions CI** — full offline `pytest` suite (**237 tests**), `pytest -k "not live"`
- **pytest + pytest-asyncio** — unit, integration, and property-style tests

## Principles

- **Zero-Dollar Stack** — no paid APIs; marginal cost per server stays at $0
- **Privacy-first** — post text never leaves infrastructure we control
- **Graceful degradation** — every stage is optional; one failed URL or a whole
  missing subsystem never aborts a run
