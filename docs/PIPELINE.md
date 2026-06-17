# SocialAgent — Ingestion Pipeline (Phases 1–7)

A local, privacy-respecting pipeline that turns **explicit public social/event URLs** into
**strictly validated, geocoded, time-resolved "Event Inspiration" records**, stores them in a
**vector database**, and surfaces them as **Discord recommendations**. Everything heavy (LLM,
embeddings, vector store) runs locally; no post text leaves the machine.

> Scope guardrails (unchanged across all phases): public, logged-out pages only; no login
> automation; no captcha/anti-bot circumvention; no crawling or discovery — only the URLs you pass
> in are fetched, one at a time, throttled per domain. Blocked pages are reported, never bypassed.

---

## 1. Total file flow

```
                                  ┌─────────────────────────────────────────────┐
   URL(s) ──▶ cli.py (main) ─────▶│ IngestionPipeline.run()   (orchestrator.py)  │
   --from-file                    │   the overarching Phase-7 manager            │
                                  └─────────────────────────────────────────────┘
                                                    │  for each URL
                                                    ▼
   ┌──────────────┐   PageSnapshot   ┌───────────────┐   RawPostSnapshot   ┌──────────────────┐
   │ browser/     │ ───────────────▶ │ extractors/   │ ──────────────────▶ │ pipeline/        │
   │ session_     │  (status, html,  │ instagram.py  │  (caption, venue,   │ validator.py     │
   │ manager.py   │   og/jsonld,     │ generic.py    │   hashtags, …)      │  + normalizer.py │
   │ (Playwright) │   text comps)    │ (base.py)     │                     │  + llm_extractor │
   └──────────────┘                  └───────────────┘                     └──────────────────┘
        Phase 1            Phase 1                Phase 1/2          Phase 2 (LLM refine)
                                                                              │ EventInspiration
                                                                              ▼
                              ┌───────────────────────────────────────────────────────┐
                              │  Phase 3  geo_enricher.py   → GeoContext (lat/lng,      │
                              │           reuses services/location_service.py (Nominatim)│
                              │  Phase 4  temporal_resolver.py → EventSchedule (UTC)    │
                              │           reuses logic/parser.py (dateparser)            │
                              └───────────────────────────────────────────────────────┘
                                                    │ validated record (or rejection)
                                   ┌────────────────┴────────────────┐
                                   ▼                                  ▼
                        ┌────────────────────┐            ┌──────────────────────┐
                        │ sinks/jsonl_sink.py│            │ sinks/chroma_sink.py │   Phase 5
                        │ data/inspirations  │            │ ChromaDB collection  │   (opt-in --chroma)
                        │ .jsonl (truth)     │            │ 'event_inspirations' │
                        └────────────────────┘            └──────────┬───────────┘
                                   │                                  │ vectors + metadata
                                   ▼                                  ▼
                        ┌────────────────────┐            ┌──────────────────────────────┐
                        │ RunReport.render() │            │ serving/recommender.py        │  Phase 6
                        │ diagnostic console │            │ serving/discord_format.py     │
                        │ (Phase 7)          │            │ serving/app.py  (FastAPI /reco)│
                        └────────────────────┘            └──────────────┬───────────────┘
                                                                          │ HTTP
                                                                          ▼
                                                          ┌──────────────────────────────┐
                                                          │ app/bot.py  /events, /sugges- │
                                                          │ tions  (Discord)              │
                                                          └──────────────────────────────┘
```

### Directory map (ingestion engine)

```
src/ingestion/
├── cli.py                     # entry point — builds components, runs the pipeline, prints report
├── config.py                  # IngestionSettings — every tunable knob + feature flags
├── browser/
│   ├── session_manager.py     # P1  SocialSessionManager (Playwright, polite, classifies outcomes)
│   └── text_isolation.py      # P1  selector profiles → TextComponents
├── extractors/
│   ├── base.py                # P1  registry + select_extractor
│   ├── instagram.py           # P1/2 IG og-tag/caption adapter
│   └── generic.py             # P1  schema.org JSON-LD / OpenGraph adapter
├── pipeline/
│   ├── normalizer.py          # P1/2 heuristics + build_llm_payload + LLM/heuristic merge
│   ├── llm_extractor.py       # P2  LlmFieldExtractor (Ollama, fail-safe ladder, anti-hallucination)
│   ├── geo_enricher.py        # P3  GeoEnricher (Nominatim reuse, confidence evaluator)
│   ├── temporal_resolver.py   # P4  TemporalResolver (candidate_times → UTC schedule)
│   ├── validator.py           # P1/2 build_record — the single strict-validation gate
│   └── orchestrator.py        # P7  IngestionPipeline + RunReport (diagnostics)
├── schemas/
│   ├── snapshot.py            # lenient intake models (PageSnapshot, RawPostSnapshot)
│   ├── extraction.py          # P2  LlmExtraction (narrow schema the model fills)
│   ├── inspiration.py         # strict EventInspiration + Geo/Schedule/Provenance submodels
│   └── results.py             # FetchStatus, IngestionResult
├── sinks/
│   ├── jsonl_sink.py          # P1  append-only JSONL (source of truth)
│   └── chroma_sink.py         # P5  ChromaSink + OllamaEmbedder (vector store)
└── serving/
    ├── recommender.py         # P6  RecommendationService (query extraction + spam guard)
    ├── discord_format.py      # P6  Discord markdown
    └── app.py                 # P6  FastAPI recommendation service

app/bot.py                     # P6  Discord bot — /events, /suggestions touch-points
recommend/{Dockerfile,requirements.txt}   # P6  recommendation service container
test_ingestion.py              # 88 offline tests + 3 live (skip-if-unavailable)
docs/PIPELINE.md               # this document
```

---

## 2. Data contract: `EventInspiration`

The one sanctioned output. Untrusted scrape data lands in lenient models first; nothing reaches a
sink unless it passes this strict (`extra="forbid"`) schema in `validator.build_record`.

| field | meaning | filled by |
|---|---|---|
| `venue_name` | the place (boilerplate like "Instagram" rejected) | P1 heuristic / P2 LLM / JSON-LD |
| `core_theme` | one-line vibe ("late-night birria pop-up") | P1/P2 |
| `category` | `EventCategory` enum | P1 keywords / P2 LLM (non-OTHER wins) |
| `geo` | `GeoContext`: raw text, place_names, lat/lng, `source`, `confidence`, `resolution` | P1 clues + P3 coords |
| `candidate_times` | raw time phrases | P1/P2 |
| `schedule` | `EventSchedule`: `status`, `start_utc`, `end_utc`, `time_known` | P4 |
| `hashtags` | normalized list | P1 |
| `provenance` | `source_url`, `platform`, `fetched_at`, `content_hash` (sha256, dedup key), `extractor`, `temporal_parser` | all |

---

## 3. Stage behavior & graceful degradation

Every external dependency can be absent; the pipeline degrades instead of failing.

| Stage | Dependency | If unavailable |
|---|---|---|
| P1 fetch | Playwright/Chromium | required; login/consent walls → reported status, not an error |
| P2 LLM | Ollama `llama3.1:8b` | `--no-llm` or Ollama down → Phase-1 heuristics; provenance tagged `+heuristic` |
| P3 geo | Nominatim (network) | `--no-geocode` / blocked / no match → `resolution=unresolved`, coords null |
| P4 time | `dateparser` (offline) | `--no-temporal` → no schedule; unparseable → `unscheduled` (kept) |
| P5 store | ChromaDB + Ollama embeddings | default off; `--chroma` + embeddings reachable, else skipped (JSONL still written) |
| P6 serve | recommend service + Ollama | bot command/auto-suggest fail quietly |

**Validation rejections** (page read OK, but dropped): empty/boilerplate venue, theme too short,
no human text, **expired event** (start in the past). Each carries a quoted reason in the report.

**Confidence evaluator (P3):** `explicit` 0.9 (page coords, no geocode call) > `geocoded` 0.75 >
`geocoded_broad` 0.45 (city-only) > `unresolved` = prior × 0.5.

**Spam prevention (P6):** intent gate (auto only) + per-channel cooldown (auto only) +
relevance-distance floor + recent-event dedup + max results. Explicit `/events` skips gate/cooldown.

---

## 4. System dependencies

**Runtime**
- Python 3.13 (repo targets 3.12; both fine), virtualenv at `.venv`.
- Playwright 1.60 + Chromium (`python -m playwright install chromium`).
- Ollama (local, `:11434`): `llama3.1:8b` (extraction), `llama3.2:1b` (embeddings). Optional — heuristics fallback.
- ChromaDB 1.5.7 (local SQLite under `data/`) — only for `--chroma` / the recommend service.
- Key libs: `pydantic` 2.13, `httpx` 0.28, `dateparser` 1.4, `requests` 2.33, `fastapi` 0.135.

**Network reality on the current machine**
- The active network performs **TLS interception**, which blocks outbound HTTPS to GitHub *and*
  Nominatim (cert/revocation errors). Consequences:
  - Live **geocoding** returns `unresolved` here — works on a non-intercepting network (home/hotspot) or with a CA fix.
  - `git push` to the team GitHub is blocked.
  - **Ollama and ChromaDB are localhost**, so P2/P4/P5 and the recommend service all work fine here.

**External services (microservice deployment, `docker-compose.yml`)**
`discord-bot` → `llm` (8001) → `db` (8002); plus the new `recommend` (8003) which bind-mounts
`./data` to read the vector store. All reach host Ollama via `host.docker.internal`.

---

## 5. Running it

```powershell
# one-time
python -m playwright install chromium
ollama pull llama3.1:8b        # + llama3.2:1b for --chroma embeddings

# ingest (full pipeline; --chroma optional)
python -m src.ingestion.cli <public-url> [<url> ...] --chroma --out data/inspirations.jsonl
#   flags: --no-llm  --no-geocode  --no-temporal  --model gemma3:4b  --headed

# recommendation service (Phase 6)
uvicorn src.ingestion.serving.app:app --port 8003
#   then in Discord:  /events late night tacos     /suggestions on

# tests
python -m pytest test_ingestion.py -v     # 88 offline; 3 live self-skip when deps absent
```

The run ends with a **diagnostic console** (`RunReport.render()`): URLs processed, validated,
rejected (with reasons), unreadable (login walls / timeouts / errors with URLs), plus a geo/schedule
breakdown of what was stored.

---

## 6. Test coverage

88 offline tests (deterministic, no network) + 3 live tests that self-skip when their dependency is
unavailable: real Ollama extraction, real Nominatim geocode (skips here — TLS), real Ollama
embeddings + Chroma roundtrip. Highlights: strict-schema accept/reject, both extractors against
HTML fixtures, the LLM fail-safe ladder & anti-hallucination cross-check (fake transport), geo
confidence tiers & fallback (fake geocoder), temporal fragment-combination/expiry (fixed clock),
Chroma dedup/metadata-safety (fake embedder), recommendation ranking/spam-guard (stub sink),
Discord formatting, and the orchestrator end-to-end over fixtures + `RunReport` aggregation.

---

## 7. Known limitations & suggested next steps

**Pending**
- **Live Instagram smoke test** — `file://` fixtures route through the *generic* extractor, not the IG
  adapter (host isn't `instagram.com`). Need one real public IG post URL to exercise `instagram.py`
  end-to-end (expect a meaningful `login_wall` rate by design).
- **No git repo initialized** — the Phase 1–7 work is uncommitted. Initializing git locally (the
  `.gitignore` already excludes `.venv`/`data`/`.env`) would checkpoint it; pushing to the team
  GitHub needs a non-intercepting network.
- **Bot/compose are delivered, not live-verified** here (no `DISCORD_TOKEN`/Docker/clean network).

**Suggested improvements (intuition)**
1. **Cross-run Chroma dedup is already content-hash based**, but JSONL appends duplicates across runs —
   add a JSONL compaction or switch the file sink to keyed upsert if it becomes the long-term store.
2. **Batch embeddings** — `OllamaEmbedder` calls `/api/embeddings` per text; batch via `/api/embed`
   for throughput when ingesting many records.
3. **Recurrence & multi-event posts** — P4 picks one start time; real captions sometimes list several
   ("every Friday", "Sat & Sun"). RRULE support + multi-schedule records is the natural extension.
4. **Reverse-geocode / venue disambiguation** — when only a city resolves (`geocoded_broad`), a second
   Overpass pass (`services/location_service.find_venues`) could pin the actual venue.
5. **Observability** — the per-stage `logging` is in place; a `--report-json` flag dumping `RunReport`
   as JSON would let a scheduled job track success/rejection rates over time.
6. **CI** — wire `pytest -k "not live"` into GitHub Actions so the 88 offline tests gate PRs.
7. **A real embedding model** — `llama3.2:1b` embeddings work but `nomic-embed-text` would improve
   retrieval quality for Phase 6 (one `ollama pull`, set `embed_model`).
