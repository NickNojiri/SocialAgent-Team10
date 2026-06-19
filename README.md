Team 10: AI Social Coordinator Agent

An autonomous social agent built with a Lean Stack to negotiate meeting times and venues between friends using local LLMs and real-world location data. This project prioritizes privacy and cost-efficiency by running models and location services locally or via free open-source APIs.
🚀 Quick Start

For development make sure to create your own virtual environment using:

`py 3.12 -m venv .venv`

Activate the environment: 

`.venv\Scripts\activate`

Then run:

`pip install -r requirements.txt`

To get the environment and agent running in a fresh Codespace:

    Automated Setup:
    Bash

    make setup

    Start the AI Engine (Tab 1):
    Bash

    make serve

    Run the Integration Test (Tab 2):
    Bash

    make test

📂 Project Structure
Plaintext

SocialAgent-Team10/
├── .devcontainer/      # Codespace environment configuration
├── src/                # Core Source Code
│   ├── logic/          # The "Brain"
│   │   ├── parser.py       # Nicholas: NLP Time Parsing
│   │   ├── scheduler.py    # Alfredo: LLM Conflict Resolution
│   │   └── database.py     # Persona & Venue Storage
│   ├── services/       # External Integrations
│   │   ├── location_service.py # John: Free OSM/Overpass Integration
│   │   └── google_maps.py      # (Optional) Premium Google Tier
│   └── ingestion/      # Phase 1 Ingestion Engine (Playwright → validated records)
├── test_unit.py        # End-to-End Integration Test Controller
├── Makefile            # Pro-tier developer shortcuts
├── requirements.txt    # Project dependencies
└── .gitignore          # Prevents .venv, secrets, and cache from being tracked

🛠️ The Tech Stack

    LLM: Llama 3.2 1B (via Ollama) – Lightweight local reasoning.

    Orchestration: LangChain – Manages the flow between tools and the LLM.

    Maps: OpenStreetMap (Nominatim & Overpass API) – Free geocoding and venue discovery.

    NLP: dateparser with custom fallback logic – Robust natural language time extraction.

    Database: ChromaDB – Vector storage for long-term user preferences (Persona memory).

    API: FastAPI (Future Implementation) – For web-based user interaction.

🧠 Key Components
NLP Parser (parser.py)

Converts messy user input like "this Friday at 7pm" into machine-readable timestamps. It includes a fallback mechanism to ensure the LLM receives context even if specific date parsing fails.
Venue Scout (location_service.py)

Calculates the geographic midpoint between participants and queries the OpenStreetMap database for high-rated venues within a reachable radius.
Scheduler (scheduler.py)

The "Agentic" heart of the project. It performs Weighted Negotiation:

    Spatial Awareness: Ensures travel fairness based on midpoint data.

    Persona Alignment: Checks venue metadata against user constraints (e.g., matching a "quiet bar" for a user who hates loud music).

    Reasoning: Provides a structured meeting plan with a clear explanation of why specific venues were chosen.

⌨️ Developer Shortcuts
Command	Action
make setup	Installs Ollama, Python libraries, and pulls the AI model.
make serve	Starts the local LLM server.
make test	Executes the full integration test (test_unit.py).
🔒 Team 10 Master Ignore

We maintain a strict .gitignore to protect privacy and repository health:

    .env (API Keys)

    __pycache__/ (Python temp files)

    .venv/ (Local environment files)

    data/ (Local database storage)

📥 Ingestion Engine

> **Note**: The Ingestion Engine feature is currently staged on the `ingestion-engine` branch. 
> To review and merge it into `main`, you can approve the pull request on GitHub or run locally:
> `git checkout main && git merge origin/ingestion-engine && git push origin main`

A local pipeline that turns explicit public post/page URLs into strictly validated "Event Inspiration" records (venue name, core theme, category, geolocation context clues) stored as JSONL for downstream phases.

- **Phase 1 — capture & heuristics:** Playwright renders the page, isolates the human-written text, and regex/keyword heuristics extract the fields.
- **Phase 2 — LLM refinement:** a local Ollama model (default `llama3.1:8b`) reads the chaotic caption text and isolates the high-fidelity fields, using Ollama's schema-constrained decoding for deterministic, valid JSON. Heuristics remain the always-available fallback, and authoritative structured data (schema.org venue, coordinates, platform location tags) is never overridden by the model.
- **Phase 3 — geographic enrichment:** location clues (`raw_location_text`, `place_names`) are resolved to coordinates via the repo's existing Nominatim helper (`src/services/location_service.py`). Each record records a `geo.resolution` (`explicit` / `geocoded` / `geocoded_broad` / `unresolved`) and a confidence scored by *how* the coordinate was obtained. Records that already carry coordinates skip geocoding entirely; a venue that won't map falls back venue→city, then degrades to `unresolved` without failing.
- **Phase 4 — temporal parsing:** raw `candidate_times` ("this Friday", "8pm", ISO strings) are resolved into a validated UTC `schedule` (start/end range) by a bridge over the repo's `TimeParser` (`src/logic/parser.py`). Relative expressions anchor to the post's capture time; expiry is judged against execution time, so historical events are rejected. Fragments like "this Friday" + "8pm" are recombined; date-only clues default the hour and flag `time_known=false`; posts with no parseable time stay as `unscheduled` inspiration.
- **Phase 5 — vector storage (opt-in `--chroma`):** validated records are upserted into a dedicated `event_inspirations` ChromaDB collection (mirroring `src/logic/database.py::PreferenceStore`), embedding a "vibe document" (theme + venue + category + tags) for similarity search, with rich metadata for filtering. Dedup is by `content_hash`, so re-ingesting a post updates one vector instead of duplicating. JSONL stays the source of truth.
- **Phase 6 — Discord recommendations:** a lightweight FastAPI service (`src/ingestion/serving/app.py`, run via `uvicorn src.ingestion.serving.app:app --port 8003` or the `recommend` Docker service) turns chat context into a vibe query, searches the collection, and returns Discord-formatted recommendations. The bot (`app/bot.py`) gets a `/events <vibe>` slash command and an opt-in `/suggestions on` auto-suggest. Spam control: intent gate + per-channel cooldown + relevance-distance floor + recent-event dedup + max results.
- **Phase 7 — orchestration & diagnostics:** `src/ingestion/pipeline/orchestrator.py` binds the whole cycle (`URL → fetch → LLM → geo → time → JSONL/Chroma`) into one `IngestionPipeline` manager; every run ends with a `RunReport` diagnostic console (validated / rejected-with-reasons / unreadable-with-status, plus a geo+schedule breakdown). The CLI is now a thin wrapper over it. **Full architecture & file flow: [docs/PIPELINE.md](docs/PIPELINE.md).**

One-time setup (after `pip install -r requirements.txt`):

`python -m playwright install chromium`
`ollama pull llama3.1:8b`  *(optional — the pipeline runs heuristics-only if Ollama is unavailable)*

Usage:

`python -m src.ingestion.cli <public-url> [<public-url> ...] [--out data/inspirations.jsonl]`
`python -m src.ingestion.cli <public-url> --no-llm`   *(skip the LLM layer)*
`python -m src.ingestion.cli <public-url> --no-geocode`   *(skip coordinate resolution)*
`python -m src.ingestion.cli <public-url> --no-temporal`   *(skip schedule resolution)*
`python -m src.ingestion.cli <public-url> --chroma`   *(also write to the ChromaDB vector store)*
`python -m src.ingestion.cli <public-url> --model gemma3:4b`   *(use a different local model)*

Tests:

`python -m pytest test_ingestion.py -v`   *(offline; the one live test self-skips when Ollama isn't running)*

How the LLM layer stays safe: the model only fills a narrow `LlmExtraction` schema — never `record_id`, provenance, hashes, or coordinates. Responses go through a fail-safe ladder (schema-constrained decode → local JSON repair → one corrective re-prompt → heuristic fallback), every field is cross-checked against the source text so an invented venue/location is discarded, and the result must still pass the same strict `EventInspiration` validation. If Ollama is down the pipeline degrades to heuristics rather than failing. The `provenance.extractor` field records which path produced each record (`…+llm:<model>` vs `…+heuristic`).

How the geo layer stays safe: it reuses `address_to_coords` unchanged (no edits to the shared service), throttled by that function's built-in 1s Nominatim delay plus an in-run query cache and a per-record query cap. A geocoder error or unreachable network is treated as a normal `unresolved` outcome — the pipeline never fails because geocoding did. Note that the same campus/proxy networks that intercept HTTPS will block Nominatim too; in that case records come back `unresolved` and need a non-intercepting network (or CA fix) to resolve.

Scope & guardrails: public, logged-out pages only — no login automation and no captcha/anti-bot circumvention. Login-walled URLs are reported with a `login_wall` status, never bypassed. URLs are fetched one at a time with per-domain throttling, and there is no crawling or discovery — only the URLs you pass in are visited. The LLM is fully local (Ollama) — no post text leaves the machine. Intended for small-scale personal/educational ingestion; automated access sits in a ToS gray area on some platforms, which is exactly why blocked statuses are first-class results.