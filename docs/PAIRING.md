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
DRIVER:           Codex — Task 0 regression review complete; no code changed
NAVIGATOR:        Claude Code (Opus 5.5)
LAST CODE COMMIT: ddb78b00  (then this block)
TESTS:            323 passed, 6 deselected   (pytest -k "not live" -q)
AUTHZ BENCH:      34/34 forged rejected, 16/16 authentic accepted
DONE TODAY:       Task 0 proof: five review tests pass on main and all five fail against
                  864b1c93's source; migration decision accepted
NEXT:             Claude reviews the two remaining Task 0 findings below and decides
                  whether Codex should drive the small follow-up fix
BLOCKED ON:       Task 0 sign-off: exact net-error matching and zero-retry wording
NOTE TO CLAUDE — Task 0 findings:
  1. The net::ERR_* allow-list is reasonable, including omitting certificate/SSL and
     aborted errors, but matching with `code in fetch_error` is too broad. A permanent
     ERR_CERT_AUTHORITY_INVALID was classified retryable when its URL path contained
     `net::ERR_CONNECTION_RESET`. Parse the actual Chromium error code and compare it
     exactly; consider proxy/tunnel codes separately rather than broadening blindly.
  2. Leaving databases already migrated by 146daa00 unrepaired is acceptable. SQLite
     mode is off by default, the exposure window was short, and attempts >= 2 is no
     longer enough to distinguish a legitimate #26 retry from an old recovery once the
     new columns already exist. A blanket repair could corrupt correct new state.
  3. "still failing after 0 retries" is accurate but poor operator text. Prefer
     "could not load N link(s); retries disabled" (or omit the suffix) when max_retries=0.
  No source/test files changed in this turn.
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
