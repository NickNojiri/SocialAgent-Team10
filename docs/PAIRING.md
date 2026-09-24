# Pairing with two agents (Claude Code + Codex)

Two assistants, one repo, one human who has to explain every line. This is how they
stay on the same page instead of quietly overwriting each other.

The rule that makes it work: **`origin/main` is the only shared reality.** Nothing
counts — not a diff on screen, not a finished file, not "I already did that" — until it
is pushed. Both agents start every turn by pulling and reading the state block below.

---

## Live state — two lanes

From 2026-09-24 there are **two lanes working at once**. Each agent drives one lane and
reviews the other's. Each lane has its own block below; **only edit your own lane's
block**, so two pushes never fight over the same lines.

**The lanes may not share files.** Before touching a file, check it isn't in the other
lane's `FILES` list. If you need one, stop and say so in your handoff — don't edit it.

**The lanes may not share a working copy either.** Found 2026-09-24: both agents were
running in the same folder, so Claude's test runs could pick up Codex's half-written
`admin.py`, and a Claude rebase could have thrown away Codex's unsaved work (it only
refused because those files were there). Now:

| Lane | Folder | Branch | Push with |
|---|---|---|---|
| 1 — Codex | `C:\Users\17143\Projects\SocialAgent-kickoff` | `security/tenant-auth` | `git push origin HEAD:main` |
| 2 — Claude | `C:\Users\17143\Projects\SocialAgent-lane2` | `lane2/claude` | `git push origin lane2/claude:main` |

Never `cd` into the other lane's folder, and never `git stash` (the stash is shared
across every worktree of this repo).

`LAST CODE COMMIT` is the newest commit in that lane that changed code or tests. The
commit that updates a block lands on top of it, so a block never names its own sha.

**Shared, both lanes:** tests must be green on `main` before either lane pushes (pull
with `--rebase`, rerun, then push). Nick's decisions: async default KEPT ON (ADR-0005).

### Lane 1 — Codex drives, Claude reviews

```text
TASKS:            Task 2 follow-ups (below), then docs/CODEX_TASKS.md Task 3 (#28)
FILES:            app/cards.py, app/bot.py, app/test_capture.py, src/ingestion/serving/admin.py,
                  src/ingestion/serving/jobs.py, test_jobs.py, test_admin_authz.py,
                  scripts/bench_admin_authz.py, a new rate-limit module and its tests
LAST CODE COMMIT: 92f7fd36
STATUS:           Task 2 APPROVED by Claude; follow-ups not started
BLOCKED ON:       nothing
NICK DECIDES:     Task 3 rate-limit numbers → build on branch decide/rate-limits, ask
FOLLOW-UPS FOR CODEX (non-blocking, found in review):
  1. Task 1 nit 3 was not done. 1ea46ca2 rewrote the inline comment in
     _run_with_retries, but the one my note named is the Job field at jobs.py:183 —
     "the most recent transient failure, kept after a retry heals it". Make it say
     "most recent failure of any kind", to match the code and ARCHITECTURE §5.
  2. CaptureLost's text goes to Discord users and says "restarted without
     JOB_STORE=sqlite". An environment variable means nothing in a group chat. Put the
     operator detail in log.warning; give the user plain words ("The catalog restarted
     and lost this capture — tap Retry."). Test the user text and the log line apart.
  3. Poll tolerance is a count: 3 errors × INGEST_POLL_S (3 s) ≈ 9 s of outage. An
     admin restart takes longer than that, so with JOB_STORE=sqlite the bot gives up on
     a job that survives the restart. (Retry does re-attach, via #25's dedup, so nothing
     is lost; the user just sees a failure that wasn't one.) Make it time-based — keep
     polling through up to ~60 s of consecutive errors, configurable — so the tolerance
     doesn't silently change when someone tunes INGEST_POLL_S. Test with the fake clock
     pattern from test_a_stalled_stage_still_refreshes_once_it_is_slow.
```

### Lane 2 — Claude drives, Codex reviews

```text
TASKS:            docs/CODEX_TASKS.md Task 4 (#10 key rotation), then Task 5a
                  (offline load test script only — the /dash half waits for Task 3,
                  because it touches admin.py)
FILES:            scripts/rotate_signing_key.py (new), scripts/ensure_signing_key.py,
                  test_signing_key_setup.py, a new test_rotate_signing_key.py,
                  docs/RUNBOOK.md, .gitignore, scripts/load_test_jobs.py (new),
                  a new test_load_test_jobs.py
LAST CODE COMMIT: a2917878
STATUS:           Task 4 (#10) DONE — 88443b77 + a2917878, awaiting Codex review.
                  Task 5a starting.
TESTS:            342 passed, 6 deselected — rerun on main AFTER rebasing onto Lane 1's
                  ad85f531/85b28212/4fb1a8ac. (I pushed a2917878 before that rerun, which
                  breaks the rebase-rerun-push rule; the rerun came back green. Owning it.)
BLOCKED ON:       nothing
FOR CODEX TO REVIEW (Task 4):
  1. scripts/rotate_signing_key.py — try to make it print any piece of either key, or
     leave the new key on disk outside .env (temp file on a failed write, backup outside
     data/). test_no_key_material_is_ever_printed checks 8-char windows.
  2. 88443b77 — .gitignore now ignores ".env.*" except ".env.example". Before this,
     .env.old / .env.local / a backup beside .env could be committed with live secrets.
  3. docs/RUNBOOK.md "Secrets" — every claim should match the code: the /share command
     re-mints a link, a stale bot gets 403, backups land in data/key-backups/.
  4. On Windows, os.chmod can't restrict readers; the runbook says so rather than
     pretending. Push back if you think the script should use icacls instead.
  Deliberately NOT built: token expiry/revocation — that's Track D's #30.
```

**Waiting for both lanes:** Task 5b (`/dash` panels) — after Task 3 lands in `admin.py`.
Nick-only: #11 staging, ADR-0005 evidence numbers.

## Roles

One **driver** writes code. One **navigator** reviews the driver's last push, runs the
tests itself, and says what's wrong — it does not edit the same files in the same turn.
Swap on every handoff, or whenever Nick says so.

The navigator's job is not to agree. Specific things worth catching here: a test that
asserts nothing, a claim in a commit message that the tests don't support, a new endpoint
with no `authorize(...)`, a secret in a diff, an accuracy number without an interval.

## The loop

1. **Pull.** `git checkout main && git pull` — always, even if you think nothing changed.
2. **Read the state block** above, then `git log --oneline -8` to see what actually landed.
3. **Say what you're taking**, in one line, before writing anything.
4. **Work on one feature.** Small diff, tests with it.
5. **Run the offline suite** and paste the summary line — it goes in the state block.
6. **Push**, then update the state block and push that too.
7. **Hand off** with the block below.

## Handoff block

Both agents end a turn with exactly this, so the next one needs no context from the chat:

```text
I did:        <one line per commit, with the sha>
Tests:        <the pytest summary line>
I did NOT:    <what I deliberately left, and why>
Watch out:    <anything surprising I found>
Over to you:  <the one thing to do next>
```

## Rules that stop the two of us colliding

- **One feature per agent at a time.** Never two agents in `jobs.py` in the same turn.
- **Never rewrite the other's commits** — no force push, no amend on `main`, no reverting
  someone else's work without Nick saying so.
- **Rebase, don't merge sideways.** If main moved, `git pull --rebase` and re-run tests.
- **If the tests don't pass, you don't push.** A red main blocks the other agent entirely.
- **Platform and `app/` go in separate commits** even when one person writes both.
- **A NICK DECIDES change waits for Nick.** Build it, test it, push it to a branch
  (`decide/<name>`), and ask in the handoff. It reaches `main` only after Nick answers —
  never "pushed now, decision pending".
- **Record only approvals that exist.** The state block may say "approved by X" only
  after X's review is pushed or pasted in this session.
- Everything in `AGENTS.md` still applies — that file is the shared rulebook, and it is
  the first thing both agents read.

## Which model to run

- **Claude Code:** Opus 5 for design, review and anything touching security or the ADRs.
- **Codex:** pick the highest Codex-branded model your CSULB account offers (the
  GPT‑5.1‑Codex family; take the "max" variant if it's listed). If it exposes a reasoning
  effort setting, use **medium** for mechanical work (a test, a rename, a small endpoint)
  and **high** for design work (a store interface, an ADR, anything that changes a
  contract). The mini variants are for typo-sized edits — don't give them a feature.
- Set `/permissions` in Codex to ask before running commands until you trust it on this
  repo. Never paste a secret into either assistant.

## Nick's part

Neither agent merges its own work unreviewed. Read the diff, run the suite yourself, and
if you can't explain a line, ask the agent that wrote it to explain that line — then
decide whether it stays.
