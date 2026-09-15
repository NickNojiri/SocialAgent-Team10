# Architecture Decision Records

One file per decision that would be expensive to reverse or that a new teammate
would otherwise have to reconstruct from commit history. Format follows
[MADR](https://adr.github.io/madr/) with an added **Evidence** section, because
several of these decisions were made by measuring, and the numbers are the point.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-chromadb-per-guild-collections.md) | ChromaDB on disk, one collection per Discord server | accepted |
| [0002](0002-heuristic-first-extraction.md) | Heuristic slot parser first; the local LLM is a gap-filler | accepted |
| [0003](0003-discord-first-thin-adapter.md) | Discord is the only client until the loop is proven; backend stays chat-agnostic | accepted |
| [0004](0004-async-capture-job-queue.md) | Reel capture becomes an async job the bot polls | proposed — spike behind `INGEST_ASYNC=1` |

Numbering is chronological by *recording* date, not decision date; the decision date
is in each file. Superseding a decision means a new ADR that links back, never an edit
that rewrites history.

## Template

```markdown
# ADR-NNNN: <decision as a short imperative sentence>

- **Status:** proposed | accepted | superseded by ADR-XXXX
- **Decided:** YYYY-MM-DD · **Recorded:** YYYY-MM-DD · **Owner:** <name>
- **Related:** ADR-…, docs/…

## Context
What situation forced a choice, and which constraints applied.

## Options considered
1. … — why not
2. … — why not
3. … — chosen

## Decision
One paragraph. What we do, in the present tense.

## Consequences
**Good:** … **Bad:** … **Follow-ups:** …

## Evidence
Commits, measurements, docs — enough that the decision could be re-derived.
```
