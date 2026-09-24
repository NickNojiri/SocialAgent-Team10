# Codex handoff — what to paste, every time

How to hand SpotBot to Codex so it does useful work instead of confident nonsense.
Setup (accounts, install, sign-in) is in [`CODEX_SETUP.md`](CODEX_SETUP.md). This page is
the *prompts*. Your own to-do list is in [`tracks/`](tracks/README.md).

Two rules before anything else:

1. **You have to be able to explain every line Codex writes.** If you can't, it doesn't go
   in the PR. That's the course requirement, not a style preference.
2. **Never paste a secret into Codex** — no `.env` contents, no Discord token, no
   `SPOTBOT_SIGNING_KEY`, no Instagram password, no session cookie. If a task seems to
   need one, it doesn't; ask Nick.

---

## 1. The handoff prompt — paste this first, every new session

Codex starts cold every time. This block is the handoff: it tells it what the project is,
what to read, what it may not do, and how to behave.

```text
You're working on SpotBot, a CSULB capstone project: a Discord bot that turns pasted
Instagram/TikTok reels into votable "spot cards" and plans outings from group chat.
Python 3.11+.

Before you change anything, read these files in this order:
  1. AGENTS.md              — the rules you must follow in this repo
  2. docs/KICKOFF.md        — what the project is and how to run it
  3. docs/tracks/README.md  — who owns what
  4. docs/tracks/TRACK-<X>.md   — my track; my features and my phase plan

I am the <TRACK NAME> specialist. Stay inside my track's paths (the ownership table in
AGENTS.md). If a task needs a file another track owns, stop and tell me instead of
editing it.

Hard limits:
- Never add a paid API or send post text off the machine. Local only: Ollama, ChromaDB,
  OpenStreetMap.
- Never commit .env, anything under data/, fixtures/labels.batch*.jsonl, or any token,
  password or cookie. Never print a secret, not even a prefix.
- Always pass encoding="utf-8" on text file reads/writes (newline="\n" when writing
  corpus/JSON) — Windows defaults to cp1252 and crashes on the label corpus.
- Don't run anything marked `live` (test_ig_live.py) — those hit real Instagram.
- Don't reformat or clean up code I didn't ask you to touch.

How I want you to work:
- Tell me your plan in three sentences and wait for me to say go.
- Make the smallest change that does the job. Add or update tests for behaviour you change.
- When you're done, run: python -m pytest -k "not live" -q — and show me the summary line.
- Then give me: what changed, why, how you tested it, and anything you were unsure about.
- If you're guessing about how something works, say so instead of guessing quietly.
```

Replace `<X>` / `<TRACK NAME>` with yours:

| Track | File | Say you are |
|---|---|---|
| A | `docs/tracks/TRACK-A-CAPTURE.md` | the Capture & Data Sources specialist |
| B | `docs/tracks/TRACK-B-ACCURACY.md` | the Accuracy & Evaluation specialist |
| C | `docs/tracks/TRACK-C-EXPERIENCE.md` | the Experience & User Research specialist |
| D | `docs/tracks/TRACK-D-CYBERSECURITY.md` | the Cybersecurity specialist |
| — | `docs/tracks/TRACK-N-PLATFORM.md` | the Architecture & Platform owner |

---

## 2. Your first real task, by track

Paste the handoff block above, wait for the plan, then paste one of these.

**Track A — the reliability harness (feature #1):**
```text
Task: scripts/capture_harness.py — a batch capture runner.
It takes a file of reel URLs, runs each one through the existing capture path, and writes
one row per URL: url, outcome, elapsed seconds, and a failure class from exactly this set:
login_wall, no_video, timeout, no_venue, not_a_place, other. It prints a summary table at
the end (count and percentage per class) and writes a JSON artifact I can chart later.
Read src/ingestion/pipeline/orchestrator.py and scripts/seed_corpus.py first — reuse the
existing pipeline, don't write a second one. Don't fetch anything while building it; I'll
run it myself on my own machine.
```

**Track B — the scorecard (feature #4):**
```text
Task: in src/ingestion/eval.py, print a 95% Wilson confidence interval next to every
accuracy number. Don't change how anything is scored — only how it's reported. Read
src/ingestion/eval.py and scripts/eval_diagnostics.py first. Then run
`python -m src.ingestion.eval --offline --split test` and show me the before and after.
Remember: tune on --split train, never iterate against --split test.
```

**Track C — the onboarding wizard (feature #8):**
```text
Task: a /setup slash command in app/bot.py that walks a new server through picking the
channel to watch for reels and setting the group's home city, and stores both per-guild.
Read app/bot.py and app/cards.py first, and follow the style of the existing commands.
Keep allowed_mentions=AllowedMentions.none() on the client — don't override it anywhere.
Any new call to :8010/:8003 that names a guild_id must send headers=tenant_headers(guild_id).
Add tests to app/test_bot.py. Start by showing me the flow as text, before writing code.
```

**Track D — the attack suite (feature #12):**
```text
Task: build on scripts/bench_admin_authz.py to make a security test suite that attacks a
LOCAL instance of our own system only — the target host is an explicit argument and the
script refuses to run against anything that isn't localhost.
One case per attack class: forged/tampered tenant token; a token for guild X used against
guild Y; a vote faked for another user; a request with no token at all. It must enumerate
every endpoint that takes a guild_id and fail if any endpoint has no case. Report: attacks
run, refused, got through — per class.
Read src/ingestion/serving/tenant_auth.py, test_admin_authz.py, scripts/bench_admin_authz.py
and docs/THREAT_MODEL.md first. Never log or print token material.
```

**Platform (Nick) — durable, idempotent capture jobs (features #23–#26):**
```text
Read AGENTS.md, docs/ARCHITECTURE.md, docs/adr/0004-async-capture-job-queue.md,
src/ingestion/serving/jobs.py, test_jobs.py, and app/bot.py::handle_reel_capture.
Stay inside the Architecture & Platform paths in ARCHITECTURE.md §7. I own Track C (app/)
as well, so app/ changes are in scope — but keep platform and app/ changes in SEPARATE PRs.

Task, in order, one PR each:
1) Add per-job stage timing to the job record using the existing on_stage callback. Add
   scripts/summarize_captures.py: the capture duration distribution, how many exceeded
   300 s and 180 s, and duplicate-capture counts.
2) Add a SQLite-backed JobStore behind the existing JobStore interface, selected by
   config (in-memory stays the default). On startup, jobs left in 'running' are requeued
   once, then marked failed with a reason. Test: simulate a crash mid-job, restart, and
   the job reaches a deterministic state.
3) Idempotent submission: key = hash(guild_id, normalized URL). A duplicate while queued
   or running returns the existing job_id. Tests: double POST, and a Retry-path resubmit,
   each produce one job.
4) Retry transient failures only (fetch timeout, network) with capped exponential
   backoff. Extraction failures never retry. Store attempts and last_error, and list
   failed jobs in the admin API.
5) Draft docs/adr/0005-sqlite-job-store.md superseding ADR-0004's in-memory choice.
   Leave the Evidence section as placeholders for my Phase 1 numbers.
6) In app/bot.py (separate PR), make the Retry button reuse the existing job_id (poll it)
   instead of resubmitting, and show "still working" once 180 s have passed, before any
   failure. Test: pressing Retry during a slow capture gives one job and one card.

Rules: no new services or dependencies (no Redis, Kafka, Postgres). Keep INGEST_ASYNC
default unchanged. Preserve the X-Tenant-Token checks on every guild call (fail closed,
503). All existing tests must pass.
```

---

## 3. Prompts for the rest of the loop

**When you don't understand the diff:**
```text
Explain this change line by line, in plain language, as if I have to defend it in a code
review tomorrow. For each line: what it does, and what breaks if it's wrong.
```

**When you're stuck on unfamiliar code:**
```text
Explain what this file does and how it connects to the rest of the project, in plain
language, before we change anything: <path>
```

**Before the PR:**
```text
Summarise this change for a PR description: what changed, why, how it was tested (paste
the pytest summary line), and anything you were unsure about. Then list every file you
touched and confirm none of them are .env, under data/, or fixtures/labels.batch*.jsonl.
```

**When Codex gets it wrong (it will):**
```text
That's not right — <say exactly what's wrong>. Don't patch over it; go back and tell me
what assumption you made that caused it, then propose the fix and wait.
```

---

## 4. What Codex is bad at here — don't ask it to

- **Judge whether an accuracy gain is real.** It will happily report a 5-point jump that
  sits inside the confidence interval. That's Track B's job, with a paired test.
- **Decide what's "secure enough."** It can write the attack case; whether a residual risk
  is acceptable is Nick's call, written down in the threat model.
- **Touch the label corpus in bulk.** `fixtures/labels.jsonl` is the project's ground
  truth. Two rows by hand, reviewed — never "clean up the corpus."
- **Run live capture.** No live tests in a sandbox. Codex builds the runner; you run it.
- **Decide the architecture.** If a task starts looking like a new service, a new
  dependency, or a second pipeline, stop and take it to Nick — that's an ADR, not a PR.

---

## 5. Definition of done, every task

1. `python -m pytest -k "not live" -q` is green and you ran it **yourself**.
2. You read the whole diff and can explain every line.
3. `git status` shows no `.env`, no `data/`, no `fixtures/labels.batch*.jsonl`.
4. The PR says what changed, why, how it was tested, and what you were unsure about.
5. One teammate who didn't write it approves. Don't merge your own.
