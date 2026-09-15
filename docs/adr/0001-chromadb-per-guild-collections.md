# ADR-0001: ChromaDB on disk, one collection per Discord server

- **Status:** accepted
- **Decided:** 2026-06 (Chroma sink), 2026-07-02 (per-guild collections) · **Recorded:** 2026-09-15 · **Owner:** Nick
- **Related:** [ADR-0002](0002-heuristic-first-extraction.md), `docs/ARCHITECTURE.md` §2, `docs/THREAT_MODEL.md` T2

## Context

Every captured spot needs two kinds of lookup: "show me the catalog, sorted by votes"
(exact metadata) and "what do we have that feels like *late-night tacos*" (similarity,
for `/events`, `/plan` and the ✨ Add-suggestions button). The project's two hard
constraints are the **zero-dollar stack** — marginal cost per server stays $0 — and
**text stays local** — post text never leaves infrastructure we control
(`docs/TEAM_TODO.md`). Once the bot could be invited to more than one server, each
server also needed its own catalog.

## Options considered

1. **SQLite + FTS5, no vectors.** Free and local, but "vibe" search over short captions
   is exactly where keyword matching fails; the recommend features would be keyword
   search with a nicer name.
2. **Postgres + pgvector.** Right answer for a hosted, multi-box deployment; wrong for
   "runs on a laptop with one script" — a database server to install, run and back up
   before anyone sees a card.
3. **Hosted vector database.** Sends every spot's text to a third party and adds a bill.
   Fails both constraints.
4. **ChromaDB `PersistentClient` on disk, embeddings from local Ollama** — chosen.
   One directory (`data/`), no server process, cosine HNSW plus a metadata filter, and
   the embedder is a localhost call.

Tenant isolation had a sub-choice: one collection with a `guild_id` metadata filter, or
one collection per guild. Per-guild was chosen: a missing `where` clause can never leak
another server's spots, and deleting a server's data is dropping one collection.

## Decision

Spots are stored twice: appended to `data/inspirations.jsonl` (the source of truth) and
upserted into a ChromaDB collection named by `chroma_sink.collection_for_guild(guild_id)`
— `event_inspirations` for the legacy single-tenant catalog and DM stash, one collection
per Discord guild otherwise. Documents are embedded with the model in
`IngestionSettings.embed_model` via Ollama; the record's `content_hash` is the id, so a
re-paste upserts instead of duplicating. The recommend service reads the same directory.

## Consequences

**Good:** no infrastructure beyond a folder; the privacy claim is literally true; tenant
isolation is structural; the JSONL means the vector store is disposable and rebuildable.

**Bad:** vectors are bound to one embedding model — a store written with
`mxbai-embed-large` (1024-dim) rejects a query embedded with anything else. That bit us:
the recommend service defaulted to `nomic-embed-text` while the admin app wrote
`mxbai-embed-large`, so every `/plan` and `/events` query on `main` failed until
`a266d9fd` (2026-09-15). Single-box only; no cross-guild queries; `PersistentClient`
is not safe to open from two processes that both write, so only the admin app writes.

**Follow-ups:** a `rebuild-from-jsonl` script for model changes; pgvector is the
migration path if a hosted tier ever needs more than one box (ADR to be written then).

## Evidence

- `src/ingestion/sinks/chroma_sink.py` — `ChromaSink`, `collection_for_guild`,
  `_vibe_document`, `_metadata`.
- `80d5989c` (2026-07-02) "guild-scoped catalogs — multi-tenant foundation".
- `32c61575` — embedding model set to `mxbai-embed-large`; `a266d9fd` — the mismatch fix
  and its regression test (`test_ingestion.py::test_recommend_service_embeds_with_the_catalog_model`).
- Constraints: `docs/TEAM_TODO.md` ("The two hard constraints"), `docs/MILESTONES.md`
  ("The engineering half of the challenge").
