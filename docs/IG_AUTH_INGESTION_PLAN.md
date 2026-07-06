# Plan: Authenticated Instagram Ingestion → API → Discord

> **Status (measured 2026-07-05, 13-URL live set):**
> - ✅ **Unauthenticated path: 100% (13/13)** caption rate via Playwright + the
>   `/embed/` fallback — *far above* the ~40–70% estimate this doc was written
>   around. On this sample the free path already meets the bar, so the bot is
>   usable today with **no Instagram account at all**.
> - ⏳ **Authenticated path: unmeasured** — the first burner login failed at IG's
>   challenge/verification step. Still worth finishing as reliability insurance
>   (walled accounts, if the unauth rate drops on a wider set), but no longer a
>   launch blocker.
>
> Sample caveat: 13 curated URLs is a small set — treat 100% as "the free path is
> much stronger than assumed," not a guarantee across all reels. Re-measure as the
> URL set grows. The authed source is built and wired into `/api/ingest` (Task 3);
> it stays dormant (Playwright path used) until a login succeeds.

## Why this plan exists

Unauthenticated scraping (Playwright + `/embed/` fallback) was *assumed* to top
out at ~40–70%. **A first real measurement contradicts that: 100% (13/13) on the
live test set** (2026-07-05) — the embed fallback recovers login-walled reels far
better than expected. This may not hold across a much larger/random URL set, so
the authed path below stays on the roadmap as insurance, but it is no longer a
launch blocker on the evidence so far.

**An authenticated session is the only path to 99%.** This plan replaces *only*
the fetch stage. Everything downstream (LLM extraction → geo enrich → temporal
resolve → ChromaDB → Discord recommendations) stays identical.

> Update (2026-07-05): the authed path is not yet measured — the first burner
> login failed at IG's challenge/verification step, so the numbers below remain
> field estimates. The unauthenticated ceiling (~40–70%) is the only measured
> figure so far.

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
