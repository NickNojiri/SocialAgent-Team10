# ADR-0005: Capture jobs are stored in SQLite, so a restart never loses one silently

- **Status:** proposed — implemented behind `JOB_STORE=sqlite` (default stays in-memory)
- **Decided:** 2026-09-23 · **Recorded:** 2026-09-23 · **Owner:** Nick
- **Supersedes:** the in-memory store chosen in [ADR-0004](0004-async-capture-job-queue.md)
  (that ADR's queue, polling and endpoints are unchanged)
- **Related:** `docs/ARCHITECTURE.md` §4, feature #24 in the 491A feature list

## Context

ADR-0004 accepted a volatile store: "a restart loses in-flight jobs — the bot sees a 404
on its next poll and shows the Retry view; accepted for the self-host." That was the
right call for a laptop demo. Two things changed it.

First, the deployment target. Feature #11 puts the service on an always-on host that
restarts for deploys, updates and crashes — restarts stop being a thing that happens
while nobody is watching.

Second, what a lost job actually looks like to a user. The 404 is indistinguishable from
"this job never existed," so the bot shows Retry for a capture that may have already
written its spot. The user presses Retry, the capture runs a second time, and until the
content-hash upsert catches it there are two cards. That is the same duplicate-work
problem ADR-0004 set out to fix, arriving through a different door.

Constraints are unchanged: zero-dollar single-box stack, no new services, no new
dependency, and `INGEST_ASYNC` stays off by default until feature #27 flips it (it did,
on 2026-09-24 — see Consequences).

## Options considered

1. **Keep it in memory, make the bot's 404 friendlier.** Cheapest, and it still can't
   tell "lost" from "never existed", so it can't stop the duplicate capture. No.
2. **Redis / a real queue (Celery, RQ, Kafka).** The standard answer, and the wrong one
   here: a second service to install, run and secure on every self-host, for a queue that
   holds single digits of jobs. Breaks the zero-dollar, one-box constraint. No.
3. **Append to a JSONL file.** No dependency, but updating a job's state means rewriting
   the file, and two writers race. The capture log (feature #23) is append-only and can
   live with that; job state can't. No.
4. **SQLite, one file, one table, behind the existing store interface.** In the standard
   library, already how ChromaDB persists, no server, transactional updates, and the
   queue keeps its interface so the endpoints don't change. **Chosen.**

## Decision

Job state lives in `JobStore`, with two implementations: `MemoryJobStore` (the default,
ADR-0004 behaviour) and `SqliteJobStore` (one file, `data/jobs.db`, selected with
`JOB_STORE=sqlite`). The queue writes at state changes — queued, running, done, failed —
not on every stage tick: the bot polls the live in-memory job, so the database only has
to be good enough to explain what a restart interrupted. A store write that fails is
logged and never fails the capture.

On startup the service calls `recover()`. Every job left `queued` or `running` gets one
more attempt; one that has already used its retry is marked `failed` with
"lost when the service restarted, after one retry". So an interrupted capture reaches a
state we can explain, always, and never loops.

## Consequences

**Good:** a deploy or crash no longer silently drops a capture; the bot can tell a lost
job from an unknown one; `attempts` gives feature #26 the field it needs for retry
policy; the in-memory default means nothing changes for a self-host that hasn't asked.

**Bad:** stage progress between state changes is still lost on a crash (the bot's live
view is memory-only); one more file to back up and to include in a data-deletion request
(feature #21); SQLite is single-writer, which is fine at one uvicorn worker and would
need revisiting if the service is ever run multi-process.

**Follow-ups:** feature #26 — done 2026-09-23 (`146daa00`): it split the restart
counter out into `recoveries` so a job that retried a timeout still gets its one
recovery, and added `last_error`; both columns are migrated in place on open.

**Changed 2026-09-24 — async is the default before staging (Nick's decision).** This
ADR planned to flip `INGEST_ASYNC` on only after it had run on staging. Feature #27
(`92f7fd36`) flipped it first; asked afterwards, Nick kept it on. Reasons: the old sync
default had a known defect — Retry re-ran the whole capture, because `/api/ingest` has
no dedup — while the async path has tests for a full queue, flaky polls and a lost job,
and is no worse than sync when the service restarts mid-capture (sync loses the request
too). `JOB_STORE` stays in-memory by default; that decision is unchanged. `INGEST_ASYNC=0`
restores the sync path, which still has no dedup. `/privacy` deletion (#21) must clear a server's
rows here too — and in `data/capture_jobs.jsonl`, the #23 timing log, which also
carries `guild_id` on every row.

## Evidence

- `src/ingestion/serving/jobs.py` — `JobStore`, `MemoryJobStore`, `SqliteJobStore`,
  `JobQueue.recover()`, `JobQueue.start()/shutdown()`.
- `test_jobs.py` — a job survives the process that wrote it; a crash mid-job is retried
  once and then failed; the memory store stays the default and forgets; sweeping a
  finished job clears the durable copy.
- Offline suite: _(placeholder — paste the `pytest -k "not live" -q` summary line from
  the Phase 1 review)_.
- Restart measurement on staging: _(placeholder — captures interrupted, recovered,
  failed after one retry, and the time from restart to the first recovered job
  finishing)_.
- Duplicate captures before vs after, from `scripts/summarize_captures.py`:
  _(placeholder — the `duplicates` line from each run)_.
