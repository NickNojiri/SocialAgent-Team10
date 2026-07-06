# Next Session Plan — location smarts, live validation, cleanup

Written 2026-07-05, after the ease-of-use v1 + OCR/fail-fast session (178 offline
tests green, tip `7bbe56e3`). Ordered by Nick's priorities; each block says
whether it's **build** (works offline) or **live** (needs Nick's machine).

## Prerequisites
- `git pull` on the home machine, `.\scripts\run_local.ps1` still boots the bot
- ffmpeg actually on PATH (was flaky last session — `where ffmpeg` in a *fresh* terminal)
- Ollama running with `llama3.1:8b` (or the `llama3.2:3b` speed profile)

---

## 1 · Location awareness — "I'm near X" (build, ~1.5h)
When a message or `/plan` mentions a place, rank suggestions by real distance.
- Parse a location phrase from the request ("near 2nd street", "we're in downtown LB")
  in `serving/recommender.py` — `synthesize_request` already extracts an `area`.
- Geocode it with the existing `src/services/location_service.py::address_to_coords`
  (injectable fake for offline tests, mirroring `TestGeoEnricher`); cache per phrase.
- Haversine distance to each candidate's stored `lat`/`lng`; sort relevance-passing
  hits by distance; `Recommendation` gains `lat`/`lng`/`distance_km`.
- `discord_format` appends "· 1.2 km away"; spot cards already have map links.
- Tests: fake-geocoder mapping, distance ordering, unchanged behavior with no location.

## 2 · Midpoint mode — fair-for-everyone suggestions (build, ~1h)
Multiple people say where they are → suggest spots near the middle.
- Parse per-user "I'm at/in X" lines from the `/plan` transcript (author → place).
- Geocode each; combine with the heritage `location_service.get_midpoint` (extend to
  N points by averaging); rank the shortlist by distance to the midpoint.
- The "Here's what I heard" card shows the inputs: "midpoint of nick (Long Beach) +
  sam (Anaheim)".
- Tests: two/three-user transcripts with a fake geocoder; single-user falls back to
  block 1 behavior.

## 3 · Make transcribe work — live validation (live, ~30m)
The Whisper path has never been proven on real reels (ffmpeg PATH issue last time).
- Fix ffmpeg, then run `docs/REEL_TRANSCRIBE_TEST_PLAN.md` (6 reels) and fill in
  its results table.
- Also trial the new OCR path live: `pip install rapidocr-onnxruntime`,
  `OCR_ENABLED=1`, capture a reel whose venue is typed on-screen; confirm the card
  picks it up.

## 4 · Ease-of-use v1 live smoke (live, ~30m)
The new bot UX is unit-tested but never live-run. In Discord:
- Paste a reel in a random channel (no setup) → progress message → morphs into the card
- `/mute` stops capture there; `/setup` shows the right state
- Break a capture on purpose (private reel) → Retry + Add-manually modal both work
- First card in a fresh server shows the one-time tip; 👍 to quorum → Lock it in →
  event appears in the server's Events tab

## 5 · Codebase cleanup (build, ~1h)
- **`requirements.txt` is UTF-16** — convert to UTF-8 so grep/tooling work on it.
- **Decide the legacy containers' fate**: the bot no longer calls `llm:8001` /
  `db:8002`. Either delete `llm/`, `db/`, and their compose services, or move them
  to `legacy/` with a README note. Same call for the heritage negotiator
  (`src/main.py`, `src/logic/scheduler.py`) — it's the meeting-coordinator era.
- **CI**: run the *full* offline suite (`pytest -k "not live"`), not just
  `test_ingestion.py`; add a ruff lint step.
- Prune scratch scripts (`scripts/diag_video.py`, demo `test_*.py` naming) and stale
  docs (`RUN_AT_WORK.md`?) — small, but the repo is going public-facing.

## 6 · Stretch (pick one if time remains)
- **TikTok extractor** (roadmap 2.1 #1) — biggest capture-coverage win.
- **`/share` command** — replies with the guild's share-page link.
- **Weekly digest** — `exporters/markdown_digest.py` exists; wire a scheduler post.

## Tests to add next session (offline, regardless of blocks)
- Recommender: location-ranked ordering + midpoint math (blocks 1–2).
- Bot handlers: extract-and-capture routing (muted channel, DM, multi-link) via a
  fake ingest client — `bot.py` currently has zero direct tests.
- Admin: `/api/manual` endpoint through TestClient with a stubbed sink.

## Carry-over notes
- Authed IG login still blocked (IP blacklisted during testing; account flagged).
  Not urgent — the free path measured 100% (13/13). If retried: fresh burner, warm
  it up in a browser first, different network, and the login-once fix is already in.
- Capture speed knobs exist (`OLLAMA_MODEL=llama3.2:3b`, `WHISPER_MODEL=tiny`,
  `TRANSCRIBE_ENABLED=0`, `SETTLE_TIMEOUT_MS=4000`) — measure the balanced profile.
