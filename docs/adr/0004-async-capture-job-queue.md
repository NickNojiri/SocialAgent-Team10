# ADR-0004: Reel capture becomes an async job the bot polls

- **Status:** accepted — feature #27 makes async the default; `INGEST_ASYNC=0` restores sync
- **Decided:** 2026-09-15 · **Recorded:** 2026-09-15 · **Owner:** Nick
- **Related:** `docs/ARCHITECTURE.md` §4, `docs/THREAT_MODEL.md` T5, `docs/PRODUCT_ROADMAP.md` §0.3

## Context

Capture is a synchronous HTTP call. The bot POSTs `/api/ingest` and waits, with a
**300 s** client timeout (`app/bot.py::handle_reel_capture`); the admin app runs every
URL in the request under a **180 s** per-URL budget (`config.capture_budget_s`) and
accepts up to **10** URLs per request. The two limits do not agree: a three-link paste
with slow transcription can take 5+ minutes on the server while the bot has already
given up at 300 s. The user sees "⚠️ Couldn't reach the catalog service", presses
**Retry**, and the retry runs the same capture again while the first is still writing —
duplicate work and, until the content-hash upsert catches it, duplicate cards. The same
coupling means a hosted deployment with several servers pasting at once would queue
inside HTTP connections, which is the wrong place for a queue.

Constraints: zero-dollar single-box stack (no new services), one uvicorn worker per
process, Whisper is CPU-bound, and Thursday's teammates must be able to run `main`
unchanged.

## Options considered

1. **Raise the timeouts.** Hides the coupling; a 10-link paste still exceeds any
   sane HTTP timeout, and the bot's status message stays frozen for the duration.
2. **Redis + a worker (arq/RQ).** The textbook answer and the right one for a
   multi-box hosted tier — but a third process to install and run, for a system whose
   whole pitch is "one script on a laptop".
3. **SQLite-backed job table + worker.** Survives restarts and needs no new service.
   More code than the problem needs today; the persistence it buys matters once jobs
   are worth more than a Retry press.
4. **In-process asyncio queue inside the admin app, polled by the bot** — chosen.
   The smallest change that removes the timeout coupling: the HTTP call returns in
   milliseconds, progress is observable, concurrency is bounded explicitly, and the
   store sits behind a small interface so option 3 is a drop-in later.

## Decision

With `INGEST_ASYNC=1` the bot calls `POST /api/jobs` (same body as `/api/ingest`) and
receives `202 {job_id}` immediately. A worker task started in the admin app's lifespan
drains a bounded `asyncio.Queue` (50 queued jobs, then HTTP 429) with
`INGEST_WORKERS` concurrent captures (default 1). The pipeline reports progress through
a new `on_stage(url, stage)` callback on `IngestionPipeline`, alongside the existing
`on_result`; the job records `stage`, `progress {done, total}` and, when finished, the
same payload `/api/ingest` returns today. The bot polls `GET /api/jobs/{id}` every 3 s,
edits its status reply as stages advance (🔎 reading → 🎙️ transcribing → 🧠 extracting →
✅), then renders cards exactly as before. Finished jobs expire after one hour.
`POST /api/ingest` keeps its synchronous contract; both paths share one `_run_ingest`.

## Consequences

**Good:** the bot never blocks on a long capture; the status line finally tells the
truth about what is happening; concurrency is a number in config instead of "however
many requests arrive"; the Retry duplicate goes away because the bot no longer
mistakes a slow job for a failure.

**Bad:** an admin-app restart loses queued and running jobs — the bot gets a 404 on
its next poll and shows the failure/Retry view. Accepted for the self-host, where the
operator restarted it. Polling adds one small request every 3 s per active capture;
fine at this scale, and replaceable by a push if it ever matters.

**Follow-ups:** flip the default once the spike has run on real captures for a sprint;
swap the in-memory `JobStore` for a SQLite table on the hosted tier; the queue is also
where per-user / per-guild rate limits (`TEAM_TODO.md` P0.5) should attach.

## Evidence

- `app/bot.py::handle_reel_capture` (300 s `httpx.AsyncClient`),
  `app/cards.py::RetryButton.callback` (same call), `src/ingestion/serving/admin.py`
  (`_MAX_URLS_PER_INGEST = 10`, `ingest()` awaits `pipeline.run`),
  `src/ingestion/config.py::capture_budget_s`.
- `src/ingestion/pipeline/orchestrator.py` — per-URL loop with `asyncio.to_thread`
  and the `on_result` hook the `on_stage` hook mirrors.
- `docs/PRODUCT_ROADMAP.md` §0.3: "Job queue for ingestion … enqueue → worker pool →
  bot edits its ⏳ message when done."
- Spike: `src/ingestion/serving/jobs.py`, `test_jobs.py`.
