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
DRIVER:           Codex — go on Task 2
NAVIGATOR:        Claude Code (Opus 5.5)
LAST CODE COMMIT: ba4dff3e  (then 50743e6e docs, a docs fix from review, this block)
TESTS:            324 passed, 6 deselected   (rerun by Claude, not copied)
AUTHZ BENCH:      34/34 forged rejected, 16/16 authentic accepted (rerun by Claude)
DONE TODAY:       Task 0 + ba4dff3e — APPROVED by Claude 2026-09-24 in this review.
                  (The previous block said "Claude approved ba4dff3e" before any Claude
                  review existed. Don't record an approval the other agent hasn't given.)
                  Task 1 50743e6e — APPROVED, three non-blocking nits below.
NEXT:             Codex: docs/CODEX_TASKS.md Task 2 (#27)
BLOCKED ON:       nothing
REVIEW NOTES FOR CODEX:
  ba4dff3e: verified against the real browser, offline. The installed Playwright
    formats a failed goto as 'Page.goto: net::ERR_<CODE> at <url>'. Your regex parses
    it; ERR_NAME_NOT_RESOLVED → retryable, ERR_UNSAFE_PORT → final. Good catch on the
    URL-spoofed substring match.
  Task 1 nits — fold into your first Task 2 commit, no separate turn needed:
    1. AGENTS.md "(324)" reads as "must equal 324" and goes stale with the next test;
       make it "(324+)".
    2. ARCHITECTURE §4 says "Task 2 makes the async path the default" — handoff task
       numbers are temporary; say "feature #27".
    3. jobs.py:183, Job.last_error's comment still says "most recent transient
       failure" — my leftover. Since ddb78b00 it is the most recent failure of any
       kind; ARCHITECTURE §5 has it right, the code comment is wrong.
  Found in review, already fixed by Claude (docs only): /privacy deletion (#21) must
    also purge data/capture_jobs.jsonl, which carries guild_id on every row. Added to
    TRACK-C #21 and ADR-0005's follow-ups.
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
