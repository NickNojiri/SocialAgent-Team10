# Next Session Plan

Refreshed 2026-07-06 after the overnight build (194 offline tests, tip `08bc74fa`).
The overnight run already shipped: location awareness + midpoint mode, the
went-there loop (100 Nights counter), the ops dashboard (`/dash`), the model
guide + bench harness, and the big cleanup (heritage stack + dead containers
removed, UTF-8 requirements, full-suite CI).

## 1 · Nick's queued asks (build, ~2h)
- **Editable cards** — an ✏️ Edit button on spot cards → modal pre-filled with
  venue/vibe → `POST /api/events/{id}/edit` updates metadata (+ best-effort
  re-embed). Card re-renders in place.
- **Card design pass** — re-imagine the embed layout (field order, blurb vs
  theme, footer) now that thumbnail/map/who's-in exist; mockups first via
  `scripts/gen_ui_mockups.py`, then implement.
- **`/browse [category]`** — see the catalog outside planning: category picker
  (food, cafe, nightlife, …), posts cards (max 5) or a compact list; "all" view.
- **UI polish pass** on the admin page + share page to match the dashboard's
  cleaner look (one accent, tabular numbers, consistent spacing).

## 2 · Live validation on Nick's machine (~1h)
- `git pull` then re-smoke the bot: paste-anywhere, progress→card, /mute, /setup,
  Retry/Add-manually, quorum → **Lock it in** → event, and the new
  **"did it happen?"** morning prompt (set `SPOT_QUORUM=1`; lock an event with a
  past time to trigger the followup within the hour).
- Run `python scripts\bench_models.py <reel.mp4>` → pick STT/LLM per
  `docs/MODEL_GUIDE.md`; record the winners in `.env`.
- Transcribe/OCR live: fix ffmpeg PATH, run `docs/REEL_TRANSCRIBE_TEST_PLAN.md`;
  try `OCR_ENABLED=1` (after `pip install rapidocr-onnxruntime`) on a reel with
  on-screen text.
- Location live: `/plan` after "im at long beach" / two people with locations →
  distances + midpoint on the picks (needs Nominatim reachable).

## 3 · Remaining build queue
- **TikTok extractor** (roadmap 2.1 #1) — fixture-based, mirrors the IG one.
- **`/share` command** — replies with the guild's share-page link.
- **Weekly digest** — `/digest` command first; scheduled auto-post later.
- Bot-handler offline tests (on_message routing: muted, DM, multi-link).

## Carry-over notes
- Authed IG login still blocked (flagged burner + blacklisted IP); free path
  measured 100% — not urgent. Fresh burner + warm-up + different network if retried.
- Dashboard capture feed is in-memory (resets with the admin app) — persist to
  JSONL later if it matters.
- Week-3 milestone prep: pick the always-on box for the dogfood deploy.
