# Codex handoff — the remaining platform work

> **Status 2026-09-24: Tasks 0–5 are all done and on `main`** (Tasks 3–5 finished by
> Claude after Nick moved Lane 1 over; details in `docs/PAIRING.md`). Kept as the record
> of what was asked. Still open: the two NICK DECIDES numbers and the Nick-only list at
> the end.

**From:** Claude Code (navigator from here on) · **To:** Codex (driver) · **For:** Nick
**Written:** 2026-09-24 · **Main at hand-off:** `917a0f33` · **Tests:** 323 passed,
6 deselected · **Authz bench:** 34/34 forged rejected, 16/16 authentic accepted

Features #23–#26 (capture timing, durable job store, one capture per link, retry policy)
are merged. This document is everything left on Nick's platform track that an agent can
do, in the order to do it. Things only Nick can do are listed at the end so nobody
mistakes them for tasks.

## How to work through this

- Follow `AGENTS.md` and the loop in `docs/PAIRING.md`: pull, read the state block,
  say what you're taking, one task per turn, green suite before every push, handoff
  block at the end, state block updated in its own commit.
- **You drive, Claude Code reviews.** After each task, stop and hand off. Don't start
  the next task in the same turn.
- **Platform and `app/` changes go in separate commits.** Nick owns both for this work.
- A task marked **NICK DECIDES** has a decision in it. Do everything around the
  decision, put a clear question in your handoff, and leave the decision point as a
  single, obvious line (a default, a constant, a flag) that Nick can flip.
- Nothing in this list touches live Instagram, the internet, or a real Discord server.
  If a task seems to need one, you've misread it — stop and ask.

---

## Task 0 — Finish the regression review of `ddb78b00`

Still open from yesterday. The state block in `docs/PAIRING.md` lists the three fixes
and one open question per fix.

- Rerun the offline suite and `python scripts/bench_admin_authz.py` yourself.
- Prove the five new tests in `test_jobs.py` (the "Codex review of #26" section) fail on
  `864b1c93`'s `src/` and pass on `main`.
- Answer the three open questions: the `net::ERR_*` allow-list, leaving databases
  already migrated by `146daa00` unrepaired, and the "after 0 retries" wording.

**Done when:** you've either signed off or listed what's still wrong. No code this turn
unless Claude Code agrees the fix is yours to drive.

---

## Task 1 — Pay the docs debt from #23–#26 (warm-up, small)

The code moved; two docs didn't.

- `docs/ARCHITECTURE.md` §4 (concurrency and time budgets) and §5 (HTTP contracts):
  add `POST /api/jobs` (now returns `duplicate`), `GET /api/jobs/{id}` (now has
  `attempts`, `last_error`, `timing`), `GET /api/jobs?guild_id=&state=failed`; the
  `JOB_STORE`, `JOB_DB`, `INGEST_MAX_RETRIES`, `INGEST_SLOW_AFTER_S` and `CAPTURE_LOG`
  switches; what "retrying" means as a stage. Link ADR-0005. Say plainly that the sync
  `/api/ingest` path still has no dedup until Task 2 lands.
- `AGENTS.md`: the suite count says "253+" — make it the real number, and add one line
  under Hard rules: *retry policy lives in `serving/jobs.py::retryable_urls` and
  `is_transient_error`; don't retry anything outside them.*

**Done when:** a reader of ARCHITECTURE.md alone could call every job endpoint correctly.
Docs only — no code.

---

## Task 2 — #27 Non-blocking capture becomes the default · NICK DECIDES the flip

The biggest user-visible item left, and it closes #25's caveat: on today's default sync
path, Retry still re-runs a capture.

Read first: ADR-0004, ADR-0005, `app/cards.py::capture_urls`, `app/bot.py::handle_reel_capture`,
`app/test_capture.py`.

1. **Queue full, handled.** `POST /api/jobs` returns 429 when the queue is full. Today the
   bot turns that into the generic "couldn't reach the catalog" text. Give it its own
   message ("Lots of captures in line — try again in a minute") and a test. (`app/`
   commit.)
2. **Poll resilience.** A single failed poll (a timeout, a 5xx) currently ends the
   capture from the bot's side. Tolerate a small number of consecutive poll errors
   before giving up, and a 404 after a restart (ADR-0005: with `JOB_STORE=sqlite` the job
   survives; without it, say so honestly). Tests for each. (`app/` commit.)
3. **The flip.** Make the async path the default in `app/cards.py`, keeping
   `INGEST_ASYNC=0` as the way back to sync. **NICK DECIDES** whether this ships now or
   after staging exists (ADR-0005 says "once this has run on staging"). Build it so the
   flip is one line, commit it last and separately, and ask in your handoff.
4. Update `.env.example`, ADR-0004's status line, and ARCHITECTURE §4 to match.

**Constraints:** every bot call keeps `headers=tenant_headers(guild_id)`; no new
dependency; no second polling loop — extend the one in `capture_urls`.

**Done when:** a paste never blocks the bot, a full queue and a flaky poll each get a
real message instead of a dead end, and the default is one reviewed line away.

---

## Task 3 — #28 Rate limits + daily cap · NICK DECIDES the numbers

A flood of links must not stall the bot or exhaust the machine.

- Limits in the admin service, not the bot — the bot is not the only thing that can reach
  `:8010`. In-memory counters are fine (single process, ADR-0004); no Redis.
- **Per server** and **per user**, plus a **daily cap** per server. Refuse with 429 and a
  `Retry-After` header.
- **Per-user identity must be signed.** Use a user-scoped tenant token
  (`mint_token(guild_id, user_id=...)`, the way votes already work) so a client can't
  dodge its limit by changing a `user_id` field. That means the bot sends a user-scoped
  token on capture — `app/` commit, kept separate.
- Emit one log line per refusal in a fixed shape Track D's monitoring (#32) can count:
  guild, user (id only), which limit, never the token.
- Proposed defaults, configurable by env — **NICK DECIDES** the real ones: 5 captures per
  user per 10 minutes, 30 per server per hour, 200 per server per day.
- Every new refusal path gets a case in `test_admin_authz.py` style tests, and the bench
  gains "evade the per-user limit by changing user_id" as a forged request.

**Done when:** a scripted flood is refused, the bot stays responsive, and changing the
user id doesn't reset anyone's limit.

---

## Task 4 — #10 Secrets management + signing-key rotation · pair with Track D

Threat-model T6. One leaked key exposes every server.

- `docs/RUNBOOK.md`: one section on where secrets live (`.env`, never the repo, never a
  log, never a fixture), and a step-by-step rotation of `SPOTBOT_SIGNING_KEY`: what to
  restart, in what order, and what breaks (every `/share` link ever issued).
- `scripts/rotate_signing_key.py`: writes a new key into `.env`, keeps a backup of the old
  file readable only by the owner, prints what to restart. **Never prints either key, not
  even a prefix.** Reuse `scripts/ensure_signing_key.py`'s parsing — it has two CRLF bugs'
  worth of history in its regex; don't write a new one.
- Tests alongside `test_signing_key_setup.py`: rotation changes the key, the old token
  stops verifying, nothing secret reaches stdout.
- **Do not build token expiry or revocation.** That is Track D's #30 on the same key.
  Leave a short note in your handoff on anything you'd want #30 to know.

**Done when:** Nick can rotate the key from the runbook alone, and the tests prove no
key material is printed.

---

## Task 5 — #29 Capture health dashboard + load test

- **Load test first**, offline: `scripts/load_test_jobs.py` drives the job queue with a
  *stubbed* capture function (sleep + fake result, never the real pipeline, never
  Instagram) at rising concurrency and reports where latency climbs and when 429s start.
  Record the numbers in the handoff.
- **Dashboard:** extend the existing operator view — `/dash` and `/api/stats` in
  `admin.py` (don't build a new app). Read what already exists: `data/capture_jobs.jsonl`
  via `scripts/summarize_captures.py`'s functions, rather than adding instrumentation.
- **Respect what `/dash` is.** It is localhost-only and deliberately *not*
  tenant-scoped, so it shows **counts and timings only — no links, no spot contents,
  no error text** (see the comment above `TenantToken` in `admin.py`, and THREAT_MODEL
  T2). Failed-job counts belong on `/dash`; the failed links themselves stay behind the
  tenant-authorized `GET /api/jobs?state=failed`. If a panel seems to need a link, it
  doesn't belong on `/dash`.
- Leave a labeled empty slot for Track D's security panel (#32).

**Done when:** one command prints the load-test result, and `/dash` shows capture
duration, captures past 3 and 5 minutes, duplicate and failed-job counts — with no link
or spot text anywhere on the page.

---

## Not for Codex — Nick only

These need accounts, money, a real server, or real numbers from live runs:

- **#11 always-on staging** — picking a host, putting secrets on it, HTTPS for share
  links, opening ports. Codex can draft the runbook section once Nick has chosen a host.
- **ADR-0005's Evidence section** — needs real restarts and real captures with
  `JOB_STORE=sqlite` and `INGEST_ASYNC=1` on Nick's machine, then
  `python scripts/summarize_captures.py`.
- **The live capture rate** (Track A #1) and anything that hits Instagram.
- **Merging.** Every task ends in a review, not a merge decision.
