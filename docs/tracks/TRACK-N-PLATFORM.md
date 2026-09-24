# Architecture & Platform — Nick

**Specialist:** Nick Nojiri · **Role:** director + platform owner
**Points:** 640 across 10 features · **Demo-day claim:** *"I designed the ingestion
architecture, made capture durable and non-blocking, and deployed it multi-tenant."*

Four features (#23–#26) were added on **September 23** and are marked **New** in the
feature list. They are mine and started now; if I don't finish one, another specialist can
take it over — each is scoped small enough to hand off.

Two jobs. **Platform:** the pipeline, the job queue, multi-tenancy, deployment, release
engineering. **Director:** priorities, review, integration, and keeping four specialists
unblocked — including making sure the handoffs in the other four docs actually happen on
the dates they say.

---

## 1. State of the platform

| Piece | Where | State |
|---|---|---|
| Pipeline orchestration | `src/ingestion/pipeline/orchestrator.py` | `IngestionPipeline` + `RunReport`, `on_stage` callback wired at fetch/transcribe/extract/save/done |
| Async job queue | `src/ingestion/serving/jobs.py`, ADR-0004/0005 | Bounded queue (429 when full), polled by the bot, **on by default** (#27). Stage timings (#23), SQLite store with restart recovery (#24, `JOB_STORE=sqlite`; on in staging), one capture per link in flight (#25), retries for timeouts only (#26), rate limits (#28, off until numbers are picked) |
| Multi-tenancy | `tenant_auth.py` (×2), ADR-0001 | HMAC tenant tokens on every guild-scoped call; one Chroma collection per guild. Bench: 47/47 forged rejected, 23/23 authentic (Sept 24) |
| Serving | `src/ingestion/serving/app.py` (:8003), `admin.py` (:8010) | Recommend/plan + admin, share, settings, feedback, delete, `/health`, `/dash` |
| Staging | `docker-compose.staging.yml`, `deploy/Caddyfile`, `scripts/deploy.py` | Built (#11); not yet running anywhere — needs a host |
| Docs | `docs/ARCHITECTURE.md`, `docs/adr/0001–0005`, `docs/SRS.md`, `docs/THREAT_MODEL.md`, `docs/RUNBOOK.md` | Current as of Sept 24 |
| Tests | `pytest -k "not live" -q` | **640+ passing** (Sept 24) |

**Open platform risks:** no always-on host yet (the demo still runs on a laptop);
Instagram often walls datacenter IPs, so a cloud host may need the authed capture path;
`mxbai-embed-large` must be pulled wherever the recommender runs (staging pulls it
itself).

---

## 2. Your features

| # | Feature | Need | Pts | Phase |
|---|---|---|---|---|
| 9 | Per-server data isolation | Must | 100 | 1 — **done Sept 17, 2026** |
| 23 | Capture timing + capture statistics — **New** | Should | 30 | 1 |
| 24 | Durable job store + crash recovery ★ — **New** | Should | 60 | 1 |
| 25 | Idempotent capture submission ★ — **New** | Should | 40 | 1 |
| 27 | Non-blocking capture with live progress ★ | Should | 80 | 2 |
| 10 | Secrets management & signing-key rotation 🔒 | Must | 60 | 2 |
| 11 | Always-on staging deployment | Must | 100 | 3 |
| 26 | Retry only what's worth retrying — **New** | Should | 40 | 3 |
| 28 | Rate limits + daily cap | Should | 30 | 4 |
| 29 | Capture health dashboard + load test | Should | 100 | 4 |

★ unique to SpotBot · 🔒 your security feature

---

## 3. To-do list

### Phase 1 — Sep 15 – Oct 3 (230 pts nominal · 130 still to do)

**#9 Per-server data isolation (100)** — shipped `684443a2`, before the phase began.
- [x] Every guild-scoped request signed for one server; votes signed for the voter;
      share links read-only.
- [x] Benchmarked: 32/32 forged rejected, 15/15 authentic accepted, 0/32 with the gate off.
- Remaining: hand the bench to Track D as the seed of their attack suite (#12).

**Features #23–#25 are the job-queue work, started Sept 23.** Read `AGENTS.md`,
`docs/ARCHITECTURE.md`, ADR-0004, `src/ingestion/serving/jobs.py`, `test_jobs.py`, and
`app/bot.py::handle_reel_capture` before touching any of them. I own `app/` for this work
as well as the platform paths — keep the two in **separate PRs** so Track C can follow
what changed in the bot.

Shared constraints for all three: **no new services or dependencies** (no Redis, Kafka,
Postgres), `INGEST_ASYNC` default unchanged, `X-Tenant-Token` checks preserved on every
guild call (fail closed, 503), all existing tests pass.

**#23 Capture timing + capture statistics — New (30)**
- [x] Per-job stage timings on the job record, via the existing `on_stage` callback — no
      second instrumentation path.
- [x] `scripts/summarize_captures.py`: duration distribution, how many captures ran past
      300 s and 180 s, and duplicate-capture counts.
- [x] **Done when:** one command prints those numbers for the reels captured so far.
- *Hand-off note:* self-contained and the smallest of the four — the easiest to give away.

**#24 Durable job store + crash recovery ★ — New (60)**
- [x] A SQLite-backed `JobStore` behind the existing interface, selected by config;
      **in-memory stays the default**.
- [x] On startup, jobs left `running` are requeued once, then marked failed with a
      reason.
- [x] Test: simulate a crash mid-job, restart, and the job reaches a deterministic state.
- [x] `docs/adr/0005-sqlite-job-store.md`, superseding ADR-0004's in-memory choice —
      Evidence section left as placeholders until the Phase 1 numbers exist.
- [x] **Done when:** a capture killed mid-run ends in a state we can explain, every time.
- *Hand-off note:* depends on #23's job record; whoever takes it needs ADR-0004 read.

**#25 Idempotent capture submission ★ — New (40)**
- [x] Submission key = hash(guild_id, normalized URL). A duplicate while queued or
      running returns the existing `job_id`.
- [x] Tests: a double POST and a Retry-path resubmit each produce one job.
- [x] Bot side, **separate PR** (`app/bot.py`): Retry polls the existing job instead of
      resubmitting, and the status line says "still working" once 180 s have passed,
      before any failure is shown. Test: pressing Retry during a slow capture gives one
      job and one card.
- [x] **Done when:** the same reel pasted twice makes one job and one card.
- **Caveat (Sept 23):** this holds on the async path (`INGEST_ASYNC=1`): the bot follows
  whatever job id the server returns, so Retry joins the running capture. The default
  sync path (`POST /api/ingest`) has no dedup — Retry there still re-runs the capture
  (the content-hash upsert stops a duplicate *row*, not the duplicate work). #27 closes
  this by making async the default. No end-to-end test drives the Discord button itself;
  the tests cover the server dedup and the client following the returned id.
- *Hand-off note:* the `app/` half is Track C's home turf — the natural person to hand
  this to if I run out of time.

### Phase 2 — Oct 6 – Oct 24 (140 pts)

**#27 Non-blocking capture with live progress ★ (80)**
- [x] Turn `INGEST_ASYNC=1` on by default — #24 is the precondition, so don't flip it
      until jobs survive a restart. *(On; Nick kept it, 2026-09-24 — ADR-0005.)*
- [x] Stage-by-stage status line updating in place: reading → listening → working out the
      venue → saving. *(`app/cards.py::stage_line`.)*
- [x] Backpressure: a full queue returns 429 and the user sees a real message, not a hang.
      *(`INGEST_MAX_QUEUED`, default 50 — see the #28 note on the number.)*
- [ ] **Done when:** a paste never blocks the bot, and the stage line is visible in a demo.

**#10 Secrets management & signing-key rotation 🔒 (60)**
- [x] One documented place for secrets — never in the repo, never in logs, never in a
      test fixture. *(`docs/RUNBOOK.md` "Secrets"; `.gitignore` now covers `.env.*`.)*
- [x] A written rotation procedure for `SPOTBOT_SIGNING_KEY`, including what must be
      re-issued afterwards (share links) and how users are told.
      *(`scripts/rotate_signing_key.py` + RUNBOOK; dry run by default, backs the old key up.)*
- [ ] Build it **with** Track D's #30 — same key, adjacent features; don't duplicate.
- [ ] **Done when:** you can rotate the key on staging without downtime and without
      breaking a live share link unannounced. Threat-model **T6**.

### Phase 3 — Oct 27 – Nov 14 (140 pts)

**#11 Always-on staging deployment (100)**
- [ ] A hosted instance running around the clock; only the ports that must be open are
      open. *(Built: `docker-compose.staging.yml` runs everything. Only `share-proxy` is
      reachable from outside, the admin app sits on loopback for `/dash` over SSH, and
      `test_staging.py` pins it. **Waiting on a host — Nick's pick** (RUNBOOK "Staging").)*
- [ ] Share links served over **HTTPS**. *(Three documented ways: Tailscale Funnel,
      Cloudflare Tunnel, or Caddy with your own domain. Live once a host runs.)*
- [x] Deploy from a tag, with the rollback documented in `docs/RUNBOOK.md`.
      *(`scripts/deploy.py`: annotated `v*` tags only; waits for health, puts the previous
      tag back if the new one fails; `--rollback`; `deploy/deploys.log`.)*
- [x] Gate the release behind Track D's security review (#13). *(Enforced: a tag deploys
      only if its message has a `Security-Review: <name>` line; `--skip-review "<why>"`
      is logged. The review itself is Track D's.)*
- [ ] **Done when:** a teammate on another network can use the bot with the laptop closed.
      *(Checklist in RUNBOOK "Staging".)*

**#26 Retry only what's worth retrying — New (40)**
- [x] Transient failures only (fetch timeout, network) retry, with capped exponential
      backoff; extraction failures never retry.
- [x] Store `attempts` and `last_error`; list failed jobs in the admin API
      (tenant-authorized like every other guild call).
- [x] **Done when:** a dropped connection recovers on its own, and a bad caption fails
      once instead of five times.
- *Hand-off note:* needs #24 in place first; independent of everything else.

### Phase 4 — Nov 17 – Dec 11 (130 pts)

**#28 Rate limits + daily cap (30)**
- [x] Per-user and per-server limits plus a daily cap, so a flood of links can't stall
      the bot or exhaust the machine. *(`serving/capture_limits.py`. Built, but every
      limit ships **off** (0) until Nick picks the numbers — `CAPTURE_*` in `.env.example`.)*
- [x] Emit a refusal event Track D's monitoring (#32) can count. *(A log line,
      `[capture_rate_limit] refused guild=… user=… limit=…`; #32 decides if it needs more.)*
- [ ] **Done when:** a scripted flood is refused and the bot stays responsive.
      *(Tested server-side; waits on real numbers. The queue depth needs one too: 50
      waiting captures can't finish inside the bot's 900 s wait — ~15 is realistic at
      ~60 s per capture on one worker. NICK DECIDES both.)*

**#29 Capture health dashboard + load test (100)**
- [ ] Capture success rate and speed over time, from Track A's harness artifact, Track
      C's time-to-card metric, and #23's stage timings — not from new, competing
      instrumentation. *(So far only #23's timings, and as totals, not over time.)*
- [x] Chart what `scripts/summarize_captures.py` already computes: duration distribution,
      captures past 3 and 5 minutes, duplicate captures. *(/dash "Capture health" tiles and
      a per-stage table, from the same `serving/capture_stats.py` the script now uses.)*
- [ ] A security panel fed by Track D's events (#32). *(Slot reserved on /dash.)*
- [ ] Load test: how many concurrent captures before it degrades; record the number.
      *(`scripts/load_test_jobs.py` finds the queue's refusal point with a stub capture;
      the real-capture number needs a real machine.)*
- [ ] **Done when:** the dashboard is live on staging, the load-test number is written
      down, and v0.1 is tagged with a 3-minute demo video.

---

## 4. Directing — the recurring work

**Weekly (pick a fixed day):**
- 15-minute standup per track: what moved, what's blocked, what's the next measurable.
- Review every open PR within 24 hours. Block on: no test, a lowered accuracy floor
  without explanation, a new endpoint with no attack case, any secret in a diff.
- Update `docs/TEAM_TODO.md` with real status. Stale plans are worse than none.

**Per phase:**
- Confirm each handoff below actually happened — a missed handoff is the failure mode
  most likely to cost this project a phase.
- Collect the phase's evidence: capture pass rate (A), scorecard (B), SUS + timings (C),
  attack report (D), staging/load numbers (you). That bundle *is* the phase deliverable.

**Handoff checkpoints you enforce:**

| When | From → To | What |
|---|---|---|
| End of P1 | A → B, D | Capture failure taxonomy; the list of outbound fetches |
| End of P1 | B → you | Scorecard + labeler/kappa numbers for the advisor brief |
| End of P1 | You → D | `bench_admin_authz.py` as the attack-suite seed |
| P1 | You → C | #25's bot-side change (Retry polls the existing job) — you own `app/` here too, so it ships as its own PR, and Track C is told what moved |
| Any phase | You → whoever | #23–#26 are yours, but each is scoped to hand off; if one is still open at a phase review, name the specialist who takes it |
| P2 | A → C | Final failure-class names |
| P2 | You → C | Stage-line contract for non-blocking capture |
| P2 | You ↔ D | Key rotation (#10) and token expiry/revocation (#30) designed together |
| P3 | A → B | Comment text entering extraction, as a labeled field |
| P3 | C → D | Deletion path to be tested |
| P3 | D → you | Security review sign-off before the staging release |
| P4 | D → B | Injection corpus for the grounding guard |
| P4 | A, C, D → you | Metrics and events for the dashboard |

---

## 5. Commands

```bash
pytest -k "not live" -q
```
```bash
python -m src.ingestion.eval --offline --split test
```
```bash
git -c http.sslVerify=false push
```

---

## 6. Still on your plate outside the feature list

- Book the ML advisor meeting (`docs/ML_ADVISOR_BRIEF.md`, `docs/ML_REVIEW_QUESTIONS.md`).
- Fill in the four teammate names in this folder and in the 491A feature list.
- Check the phase dates above against Canvas before the team commits to them.
- Update `docs/CAPSTONE_PLAN.md` to the five-specialist framing when you're ready — it
  still says Tracks A/B/C + Platform.
