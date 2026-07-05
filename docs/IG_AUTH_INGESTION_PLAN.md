# Plan: Authenticated Instagram Ingestion → API → Discord

> **Status:** ✅ **MEASURED — authed path hit 100% caption rate** on the 13-URL
> live set (`test_ig_live.py::test_authed_pass_rate`, 2026-07-05, burner account).
> This clears the ≥95% go/no-go bar → the authenticated fetch source is the
> chosen path and the hosted product is viable on reliability grounds.
> Source built and shipped: `src/ingestion/sources/ig_authed.py` + config/env
> plumbing + 7 offline tests. Next: widen the URL set over time to keep the
> number honest, and wire the source into the live `/api/ingest` path (Task 3).

## Why this plan exists

Unauthenticated scraping (Playwright + `/embed/` fallback) cannot reach the 99%
reliability goal. Instagram walls unauthenticated traffic aggressively in 2026,
and `igsh=` share links are flagged as app-referred and walled hardest. Measured
ceiling for the unauthenticated path: **~40–70%**.

**An authenticated session is the only path to 99%.** This plan replaces *only*
the fetch stage. Everything downstream (LLM extraction → geo enrich → temporal
resolve → ChromaDB → Discord recommendations) stays identical.

> Update (2026-07-05): first real-network measurement is in — the authed path
> returned a caption for **13/13 URLs (100%)**. Sample is small; treat 100% as
> "comfortably above the 95% bar" rather than a literal guarantee, and re-measure
> as the URL set grows. The unauthenticated ceiling below (~40–70%) stands.

---

## Architecture

```
Burner IG account  ──login once──►  session.json  (cookie, reused, no re-login)
        │
        ▼
  instagrapi.Client.media_info_by_shortcode(code)
        │  → caption, location (name + lat/lng), timestamp, hashtags
        ▼
  Adapt into RawPostSnapshot  (the schema the pipeline already consumes)
        │
        ▼
  EXISTING pipeline: build_record → LLM → geo → temporal → JSONL + ChromaDB
        │
        ▼
  POST /ingest  (new FastAPI route)  ◄── Discord bot OR cron triggers a pull
        │
        ▼
  Discord bot → /events or auto-suggest → thread (already built)
```

---

## Decision still open: fetch source

| Option | Reliability | Cost | ToS risk | Notes |
|---|---|---|---|---|
| **instagrapi + burner** (this plan) | ~95–99% | Free | Burner can be flagged | Never use a real account |
| Paid resolver (RapidAPI/Apify) | ~99% | ~$30–50/mo | None to you | Swap the source module's internals only |
| Unauthenticated (current) | ~40–70% | Free | None | Will not hit 99% |

The module boundary below is identical for options 1 and 2 — only the body of
`fetch_shortcode()` changes. Pick at build time.

---

## Build tasks

### Task 1 — `src/ingestion/sources/ig_authed.py` (new)
A source module that returns the pipeline's existing `RawPostSnapshot`.

- `class AuthedInstagramSource:`
  - `__init__(self, settings)` — load `session.json` if present; else login with
    `IG_USERNAME`/`IG_PASSWORD` from env and **persist the session** so login
    happens once. Never log credentials.
  - `fetch_shortcode(self, shortcode: str) -> RawPostSnapshot | None`
    - `media = client.media_info_by_shortcode(shortcode)`
    - Map: `caption = media.caption_text`, `author_handle = media.user.username`,
      `location_text = media.location.name if media.location else None`,
      `hashtags = re.findall(...)`, `fetched_at = now`.
    - If `media.location` has `lat`/`lng`, pass them through so the geo enricher
      records `EXPLICIT` (no Nominatim call needed — faster + more accurate).
    - Catch `LoginRequired`/`ClientError` → return `None` (degrade, never crash).
- Keep it injectable/testable: accept an optional `client` arg so unit tests can
  pass a fake (mirror how `LlmFieldExtractor`/`GeoEnricher` take injected deps).

### Task 2 — Wire location coords into the snapshot path
`RawPostSnapshot` / `PageSnapshot` currently surface coords only via JSON-LD.
Confirm the authed source can set explicit lat/lng so `GeoEnricher._resolve`
hits the `EXPLICIT` branch (geo_enricher.py line ~70). If the schema can't carry
coords from a RawPostSnapshot, add an optional `geo_lat`/`geo_lng` field and read
it in `pipeline/validator.py::build_record`.

### Task 3 — `POST /ingest` endpoint in `src/ingestion/serving/app.py`
```python
class IngestRequest(BaseModel):
    shortcodes: list[str] = []      # explicit reels to pull
    # (future) hashtag / location / account-feed pulls

@app.post("/ingest")
def ingest(req: IngestRequest):
    # run AuthedInstagramSource → existing IngestionPipeline → return RunReport.to_dict()
```
Reuse `IngestionPipeline`; just feed it snapshots from the authed source instead
of `SocialSessionManager`. Return the existing `RunReport` JSON so the caller
sees validated/rejected/unreadable counts.

### Task 4 — Config (`src/ingestion/config.py` + `.env.example`)
Add: `ig_username`, `ig_password`, `ig_session_path` (default `data/ig_session.json`).
Document in `.env.example`. **Add `data/ig_session.json` to `.gitignore`** — never
commit a session cookie.

### Task 5 — Tests
- `test_ig_authed.py` (offline): inject a fake instagrapi client returning a
  canned `media` object; assert the `RawPostSnapshot` mapping is correct,
  including the emoji/location/coords path and the `None`-on-error degrade.
- Extend `test_ig_live.py` with an authed live mode (skipped unless
  `IG_USERNAME` is set) that pulls the 13 real shortcodes and reports the
  measured pass rate — this is the number that proves/disproves 99%.

### Task 6 — Optional: scheduled background pulls
A cron/loop that pulls latest posts from a configured set of accounts/hashtags
every N hours and ingests new ones, so ChromaDB stays fresh without manual
triggers. Defer until the on-demand `/ingest` path is proven.

---

## Sequencing
1. Task 4 (config) → Task 1 (source) → Task 5 offline tests — all doable without
   a live account.
2. On a real network with a burner account: run Task 5 live mode → **record the
   measured pass rate** (replace the estimates in this doc).
3. If ≥95%: wire Task 3 (`/ingest`) + Task 2 (coords) and connect the Discord bot.
4. Task 6 last.

## Risks
- **Account flagging:** use a dedicated burner; add a polite delay between pulls;
  reuse the saved session (don't re-login each run).
- **instagrapi breakage:** IG changes break scrapers periodically. The paid-API
  option is the fallback — same module boundary, swap the body of `fetch_shortcode`.
- **Credential safety:** session file gitignored; creds only from env; never logged.
