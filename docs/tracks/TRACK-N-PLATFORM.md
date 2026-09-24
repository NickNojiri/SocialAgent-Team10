# Architecture & Platform — Nick

**Specialist:** Nick Nojiri · **Role:** director + platform owner
**Points:** 570 across 7 features · **Demo-day claim:** *"I designed the ingestion
architecture, made capture durable and non-blocking, and deployed it multi-tenant."*

Two jobs. **Platform:** the pipeline, the job queue, multi-tenancy, deployment, release
engineering. **Director:** priorities, review, integration, and keeping four specialists
unblocked — including making sure the handoffs in the other four docs actually happen on
the dates they say.

---

## 1. State of the platform

| Piece | Where | State |
|---|---|---|
| Pipeline orchestration | `src/ingestion/pipeline/orchestrator.py` | `IngestionPipeline` + `RunReport`, `on_stage` callback wired at fetch/transcribe/extract/save/done |
| Async job queue | `src/ingestion/serving/jobs.py`, ADR-0004 | In-process `asyncio.Queue`, bounded (429 when full), polled by the bot, behind `INGEST_ASYNC=1` — **off by default** |
| Multi-tenancy | `tenant_auth.py` (×2), ADR-0001 | HMAC tenant tokens on every guild-scoped call; one Chroma collection per guild. 32/32 forged rejected |
| Serving | `src/ingestion/serving/app.py` (:8003), `admin.py` (:8010) | Recommend/plan + admin & share |
| Docs | `docs/ARCHITECTURE.md`, `docs/adr/0001–0004`, `docs/SRS.md`, `docs/THREAT_MODEL.md`, `docs/RUNBOOK.md` | Current as of Sept 17 |
| Tests | `pytest -k "not live" -q` | **291 passing** (Sept 17) |

**Open platform risks:** no always-on host (demo runs on a laptop today); no rate
limiting; the job queue is in-process, so a restart loses in-flight work and the same reel
pasted twice is captured twice (both closed by #23); `mxbai-embed-large`
must be pulled on any machine that runs the recommender, or Chroma throws a dimension
error.

---

## 2. Your features

| # | Feature | Need | Pts | Phase |
|---|---|---|---|---|
| 9 | Per-server data isolation | Must | 100 | 1 — **done Sept 17, 2026** |
| 23 | Durable, idempotent capture jobs ★ | Should | 100 | 1 |
| 24 | Non-blocking capture with live progress ★ | Should | 80 | 2 |
| 10 | Secrets management & signing-key rotation 🔒 | Must | 60 | 2 |
| 11 | Always-on staging deployment | Must | 100 | 3 |
| 25 | Rate limits + daily cap | Should | 30 | 3 |
| 26 | Capture health dashboard + load test | Should | 100 | 4 |

★ unique to SpotBot · 🔒 your security feature

---

## 3. To-do list

### Phase 1 — Sep 15 – Oct 3 (200 pts nominal · 100 still to do)

**#9 Per-server data isolation (100)** — shipped `684443a2`, before the phase began.
- [x] Every guild-scoped request signed for one server; votes signed for the voter;
      share links read-only.
- [x] Benchmarked: 32/32 forged rejected, 15/15 authentic accepted, 0/32 with the gate off.
- Remaining: hand the bench to Track D as the seed of their attack suite (#12).

**#23 Durable, idempotent capture jobs ★ (100)** — in progress, five PRs in order.
Read `AGENTS.md`, `docs/ARCHITECTURE.md`, ADR-0004, `src/ingestion/serving/jobs.py`,
`test_jobs.py`, and `app/bot.py::handle_reel_capture` first. You own `app/` for this piece
as well as the platform paths — keep the two in **separate PRs** so Track C can follow
what changed in the bot.
- [ ] **PR 1 — timing.** Per-job stage timings on the job record, via the existing
      `on_stage` callback. Add `scripts/summarize_captures.py`: duration distribution,
      how many captures ran past 300 s and 180 s, and duplicate-capture counts.
- [ ] **PR 2 — durability.** A SQLite-backed `JobStore` behind the existing interface,
      selected by config; **in-memory stays the default**. On startup, jobs left
      `running` are requeued once, then marked failed with a reason. Test: simulate a
      crash mid-job, restart, and the job reaches a deterministic state.
- [ ] **PR 3 — idempotency.** Submission key = hash(guild_id, normalized URL). A
      duplicate while queued or running returns the existing `job_id`. Tests: a double
      POST and a Retry-path resubmit each produce one job.
- [ ] **PR 4 — retries.** Transient failures only (fetch timeout, network) with capped
      exponential backoff; extraction failures never retry. Store `attempts` and
      `last_error`; list failed jobs in the admin API (tenant-authorized like everything
      else).
- [ ] **PR 5 — the decision record.** `docs/adr/0005-sqlite-job-store.md`, superseding
      ADR-0004's in-memory choice. Leave the Evidence section as placeholders until the
      Phase 1 numbers exist.
- [ ] **PR 6 — the bot side (`app/`, kept separate).** The Retry button polls the
      existing `job_id` instead of resubmitting, and the status line says "still working"
      once 180 s have passed, before any failure is shown. Test: pressing Retry during a
      slow capture gives one job and one card.
- Constraints: **no new services or dependencies** (no Redis, Kafka, Postgres),
  `INGEST_ASYNC` default unchanged, `X-Tenant-Token` checks preserved on every guild call
  (fail closed, 503), all existing tests pass.
- [ ] **Done when:** a capture survives a restart with a deterministic outcome, the same
      reel pasted twice makes one job, and `summarize_captures.py` prints the duration
      distribution and duplicate count.

### Phase 2 — Oct 6 – Oct 24 (140 pts)

**#24 Non-blocking capture with live progress ★ (80)**
- [ ] Turn `INGEST_ASYNC=1` on by default — #23 is the precondition, so don't flip it
      until jobs survive a restart.
- [ ] Stage-by-stage status line updating in place: reading → listening → working out the
      venue → saving.
- [ ] Backpressure: a full queue returns 429 and the user sees a real message, not a hang.
- [ ] **Done when:** a paste never blocks the bot, and the stage line is visible in a demo.

**#10 Secrets management & signing-key rotation 🔒 (60)**
- [ ] One documented place for secrets — never in the repo, never in logs, never in a
      test fixture.
- [ ] A written rotation procedure for `SPOTBOT_SIGNING_KEY`, including what must be
      re-issued afterwards (share links) and how users are told.
- [ ] Build it **with** Track D's #27 — same key, adjacent features; don't duplicate.
- [ ] **Done when:** you can rotate the key on staging without downtime and without
      breaking a live share link unannounced. Threat-model **T6**.

### Phase 3 — Oct 27 – Nov 14 (130 pts)

**#11 Always-on staging deployment (100)**
- [ ] A hosted instance running around the clock; only the ports that must be open are
      open.
- [ ] Share links served over **HTTPS**.
- [ ] Deploy from a tag, with the rollback documented in `docs/RUNBOOK.md`.
- [ ] Gate the release behind Track D's security review (#13).
- [ ] **Done when:** a teammate on another network can use the bot with the laptop closed.

**#25 Rate limits + daily cap (30)**
- [ ] Per-user and per-server limits plus a daily cap, so a flood of links can't stall
      the bot or exhaust the machine.
- [ ] Emit a refusal event Track D's monitoring (#29) can count.
- [ ] **Done when:** a scripted flood is refused and the bot stays responsive.

### Phase 4 — Nov 17 – Dec 11 (100 pts)

**#26 Capture health dashboard + load test (100)**
- [ ] Capture success rate and speed over time, from Track A's harness artifact, Track
      C's time-to-card metric, and #23's stage timings — not from new, competing
      instrumentation.
- [ ] Chart what `scripts/summarize_captures.py` already computes: duration distribution,
      captures past 3 and 5 minutes, duplicate captures.
- [ ] A security panel fed by Track D's events (#29).
- [ ] Load test: how many concurrent captures before it degrades; record the number.
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
| P1 | You → C | #23's bot-side change (Retry polls the existing job) — you own `app/` here too, so it ships as its own PR, and Track C is told what moved |
| P2 | A → C | Final failure-class names |
| P2 | You → C | Stage-line contract for non-blocking capture |
| P2 | You ↔ D | Key rotation (#10) and token expiry/revocation (#27) designed together |
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
