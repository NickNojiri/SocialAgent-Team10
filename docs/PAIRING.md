# Pairing with two agents (Claude Code + Codex)

Two assistants, one repo, one human who has to explain every line. This is how they
stay on the same page instead of quietly overwriting each other.

The rule that makes it work: **`origin/main` is the only shared reality.** Nothing
counts — not a diff on screen, not a finished file, not "I already did that" — until it
is pushed. Both agents start every turn by pulling and reading the state block below.

---

## Live state

Whoever finishes a turn overwrites this block, commits it on its own, and pushes.

`LAST CODE COMMIT` is the newest commit that changed code or tests. The commit that
updates this block always lands on top of it, so a block can never name its own sha —
compare against `git log`, not against the tip. (Codex caught the first version of
this block claiming a tip that had already moved.)

```text
SESSION:          2026-09-24
DRIVER:           Codex — go on Task 2 follow-ups (below), then Task 3
NAVIGATOR:        Claude Code (Opus 5.5)
LAST CODE COMMIT: 92f7fd36  (then an ADR-0005 decision record, then this block)
TESTS:            329 passed, 6 deselected   (rerun by Claude, not copied)
AUTHZ BENCH:      34/34 forged rejected, 16/16 authentic accepted (rerun by Claude)
DONE TODAY:       Task 0 + ba4dff3e — approved. Task 1 50743e6e + 1ea46ca2 — approved.
                  Task 2 #27 6375587b, 4009ba12, 6e25f617, 92f7fd36 — APPROVED by Claude,
                  with three follow-ups below.
DECISION:         Nick KEEPS the async default on (asked 2026-09-24, after 92f7fd36 had
                  already been pushed). Recorded in ADR-0005 Consequences.
PROCESS NOTE:     92f7fd36 was a NICK DECIDES change and was pushed to main before Nick
                  decided, while this block called the decision "pending". Rule added
                  below: a NICK DECIDES commit waits on a branch until Nick answers.
NEXT:             Codex: the three follow-ups in one small commit each, then Task 3.
BLOCKED ON:       nothing
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
