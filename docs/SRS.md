# SpotBot — Software Requirements Specification

Version 1.0 draft · 2026-09-15 · Owner: Nick · Status: **draft for advisor review**
(the CSULB template, if one is required, has not been confirmed — see §8).

This SRS is written the way the project is run: every requirement has an ID, a way to
check it, and — for the non-functional ones — the number it is at today and the command
that produced that number. Numbers are the held-out figures from
`python -m src.ingestion.eval --offline --split test` and
`python scripts/eval_diagnostics.py` on 2026-09-15.

---

## 1. Introduction

### 1.1 Purpose
Specify what SpotBot must do and how well, so that the four-person capstone team can
build against it, test against it, and be graded against it.

### 1.2 Scope
SpotBot is a Discord bot for friend groups. A member pastes an Instagram reel (or
TikTok) of a place; the bot reads the caption and the spoken audio, works out the venue,
category and location, and posts a **spot card** the group votes on. When enough people
are in, it creates a Discord Scheduled Event; the next day it asks whether they went.
`/plan` reads the recent chat and proposes an outing from the saved spots. A web page
shares the catalog read-only. Everything runs on one machine with no paid services.

Out of scope for the capstone: payments, mobile apps, custom accounts, crawling or
discovery of content, any chat platform other than Discord (see ADR-0003).

### 1.3 Definitions
| Term | Meaning |
|---|---|
| **Reel** | A public Instagram reel/post or TikTok video URL a user pastes |
| **Capture** | Fetch → transcribe → extract → validate → store, for one URL |
| **Spot** | One validated `EventInspiration` record: venue, category, theme, location, optional schedule and summary |
| **Card** | The Discord embed for a spot, with persistent buttons |
| **Catalog** | All spots for one Discord server (guild); DMs have a personal stash |
| **Quorum** | Number of 👍 "Want to go" votes (default 3) at which the bot offers to lock in an event |
| **Went-there loop** | The morning-after prompt; two confirmations = one **night out** |
| **Held-out** | The 30% test split of the label corpus, never used for tuning |

### 1.4 References
`README.md`, `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/THREAT_MODEL.md`,
`docs/EXTRACTION_ACCURACY.md`, `docs/ML_REVIEW_QUESTIONS.md`, `docs/CAPSTONE_PLAN.md`.

---

## 2. Overall description

### 2.1 Product perspective
Three processes on one host — a Discord gateway bot (`app/bot.py`), an admin/ingest
HTTP service (`:8010`), a recommend service (`:8003`) — plus local Ollama. Storage is an
append-only JSONL file and a ChromaDB directory. See `ARCHITECTURE.md` §1–2.

### 2.2 Users
| Persona | Wants | Touches |
|---|---|---|
| **Sharer** | paste a reel and see it become a card, without learning commands | DM / channel paste, Retry, manual add |
| **Planner** | turn "we should go" into a date | votes, Lock it in, `/plan`, `/events` |
| **Server admin** | control where the bot listens, remove junk | `/mute`, `/suggestions`, Remove, `/setup` |
| **Self-hoster / operator** | run it on a laptop in one script and keep it running | `setup.ps1`, `run_local.ps1`, `/dash`, `.env` |
| **Grader / advisor** | verify claims from the repo alone | tests, eval commands, docs |

### 2.3 Operating environment
Windows 11 (PowerShell scripts) or Linux/macOS by hand; Python 3.12–3.13; Ollama with
`mxbai-embed-large` (required) and `llama3.1:8b` (optional); Playwright Chromium;
`ffmpeg` + `faster-whisper` for audio; no GPU. Internet access to Instagram/TikTok and,
for the CLI path only, Nominatim.

### 2.4 Constraints
- **C-1 Zero-dollar stack.** Marginal cost per server stays $0 — no paid Places, vision,
  LLM or hosting APIs in the default path.
- **C-2 Text stays local.** Caption and transcript text is processed only on
  operator-controlled infrastructure. A hosted model may be used only as an explicit,
  disclosed opt-in (ADR-0002).
- **C-3 User-initiated only.** The bot fetches exactly the public URLs users paste,
  one at a time, throttled per domain. No crawling, no login automation on personal
  accounts, no captcha bypass; a login wall is reported, never circumvented.
- **C-4 Single box.** One uvicorn worker per service; no Redis/Postgres.
- **C-5 Discord platform rules.** Message Content intent; persistent components.

### 2.5 Assumptions
Users share reels of *places* (not recipes/memes) most of the time; Instagram's
unauthenticated pages and `/embed/` fallback keep working; a burner account, if used,
is dedicated and disposable.

---

## 3. Functional requirements

Verification column: **T** = automated test exists, **M** = manual check, **P** = planned.

### 3.1 Capture
| ID | Requirement | Verify |
|---|---|---|
| FR-C1 | Pasting a supported link in a DM, any readable channel, or an `@mention` triggers capture with no command. | T `app/test_bot.py` |
| FR-C2 | A message with several links captures each once, duplicates collapsed, up to 10 per request. | T `test_serving_extra.py`; P multi-link routing test (Track C starter) |
| FR-C3 | The bot replies with a status message within 1 s and edits it into the card (or failure) when done. | M; P stage-level progress (ADR-0004) |
| FR-C4 | A failed capture offers **Retry** and **Add manually** (modal: venue, vibe, link); a paste is never a dead end. | T `app/test_cards.py`, `test_serving_extra.py::test_manual_record_builder` |
| FR-C5 | Capture can be turned off per channel (`/mute`); muted channels are ignored. | T `app/test_bot.py` |
| FR-C6 | Each Discord server has its own catalog; DM captures go to the sharer's personal stash. | T `test_serving_extra.py::test_recommend_routes_by_guild` |
| FR-C7 | Supported sources: Instagram reels/posts (unauthenticated, `/embed/` fallback, optional authed path), TikTok. | T `test_ig_embed.py`, `test_ig_authed.py`, `test_tiktok.py` |
| FR-C8 | Re-pasting a known reel returns the existing spot marked "already saved" (dedup by content hash). | T `test_ingestion.py` (Chroma dedup) |

### 3.2 Extraction
| ID | Requirement | Verify |
|---|---|---|
| FR-X1 | From caption, transcript, on-screen text and the platform location tag, produce venue, category (8-class enum), city/location text, hashtags. | T `test_extraction_labels.py` + eval |
| FR-X2 | When the reel has audio, transcribe it locally and use it as a venue/location signal. | T `test_transcriber.py` |
| FR-X3 | Show a quick description from the audio on the card, or "No info" when nothing was transcribed. | T `app/test_cards.py` |
| FR-X4 | Reject posts that are not a specific place (recipes, promos, lists) with a stated reason; never crash on one bad URL. | T `test_ingestion.py` (validator, orchestrator) |
| FR-X5 | Record which slot produced the venue and a 0–1 confidence with every spot. | T schema `venue_slot`/`venue_confidence` |
| FR-X6 | Attach the post thumbnail and a map link when coordinates are known. | T `app/test_cards.py` |
| FR-X7 | Parse time phrases into a schedule; drop already-expired events. | T `test_ingestion.py` (temporal) |

### 3.3 Voting, planning, follow-through
| ID | Requirement | Verify |
|---|---|---|
| FR-V1 | 👍 Want to go / 👎 Not for me, with the voter's identity; the card shows *who* is in. | T `test_serving_extra.py::test_apply_vote_identity_and_anonymous` |
| FR-V2 | At quorum (`SPOT_QUORUM`, default 3) the bot offers **Lock it in**, which creates a Discord Scheduled Event with the spot's time (or next Friday 7 pm) and map link. | T `app/test_cards.py`; M live |
| FR-V3 | The morning after a locked-in event, ask the channel whether it happened; two confirmations count one night out (`/api/nights`). | T `test_went.py` |
| FR-V4 | `/plan` reads the last 25 messages, opens a thread with "what I heard" and up to N picks as votable cards; pinned/attended spots are marked. | T `test_ml_layers.py`, `test_serving.py`; M |
| FR-V5 | `/events <vibe>` returns matching spots; ambient suggestions are opt-in per channel with cooldown and dedup. | T `test_serving_extra.py` |
| FR-V6 | `/browse`, `/catalog`, `/digest`, `/share`, `/setup`, `/help` as documented in `README.md`. | M |
| FR-V7 | ✏️ Edit opens a pre-filled form; changes persist and re-embed best-effort. | T `app/test_cards.py` |
| FR-V8 | Remove requires *Manage Messages* in a server. | T `app/test_cards.py` |
| FR-V9 | Buttons keep working after a bot restart. | T `register_dynamic_items` coverage |

### 3.4 Web and API
| ID | Requirement | Verify |
|---|---|---|
| FR-W1 | Admin page: list, vote, remove, add URLs. | M |
| FR-W2 | Public share page per server is read-only (no write controls). | T `test_serving_extra.py::test_share_page_is_served_and_read_only` |
| FR-W3 | `/dash` shows services, captures feed, per-server counts. | M |
| FR-W4 | The HTTP API in `ARCHITECTURE.md` §5 is the only way the bot touches data. | T `test_serving*.py` |

### 3.5 Operations
| ID | Requirement | Verify |
|---|---|---|
| FR-O1 | One setup script installs everything a teammate needs; one run script starts all three processes. | M (Week 0 kickoff) |
| FR-O2 | The offline test suite and the eval scorecard run with no Discord token, no network. | T CI |
| FR-O3 | All behaviour switches are `.env` variables documented in `README.md`. | M |

---

## 4. Non-functional requirements

Each row: what it is today (measured), the target, and who moves it.

| ID | Quality | Today (measured) | Target | Owner |
|---|---|---|---|---|
| NFR-1 | **Capture success rate** | 13/13 on a curated set (2026-07-05); **unmeasured on random reels** | ≥ 95% on a random set of ≥ 50, per release | Track A |
| NFR-2 | **Time-to-card** | ~20–40 s incl. Whisper on CPU; p50/p95 not measured | < 15 s p50 (`TRANSCRIBE_ENABLED=0` profile), measured | Track C |
| NFR-3 | **Venue accuracy (held-out)** | exact **37.4%** (43/115), 95% CI [29.1, 46.5]; extractive ceiling 78% | report against the ceiling; any claimed gain must clear a paired test (McNemar) on the same rows | Track B |
| NFR-4 | **Category accuracy** | **63.2%** [54.2, 71.4]; majority baseline 54.7%; macro 63.8% | beat the majority baseline outside its interval | Track B |
| NFR-5 | **Promo rejection** | precision **50%** (4/8), recall 23.5% [9.6, 47.3] | precision ≥ 90% before any recall work — never throw away a real place | Track B |
| NFR-6 | **City accuracy** | **73.1%** [63.3, 81.1] | hold; geocoding is not the bottleneck | Track B |
| NFR-7 | **Privacy** | text local by construction (Ollama, on-disk Chroma) | unchanged; hosted LLM only opt-in + disclosed | Nick |
| NFR-8 | **Cost** | $0 marginal | $0 marginal | Nick |
| NFR-9 | **Graceful degradation** | every optional stage can be absent; one URL never aborts a run | keep; regression tests per stage | all |
| NFR-10 | **Test coverage** | 238 offline tests, CI on every push; accuracy floors on `--split train` | grows with every feature; floors ratchet up only | all |
| NFR-11 | **Security** | see `THREAT_MODEL.md`: mentions fixed (T1); tenant authz fixed — 32/32 forgeries rejected, `scripts/bench_admin_authz.py` (T2); SSRF open (T3) | T3 closed by end of Sprint 3 | Nick |
| NFR-12 | **Portability** | Windows scripts; Linux/macOS by hand; Python 3.12–3.13 | Linux script parity for the always-on box | Nick |
| NFR-13 | **Resource envelope** | no GPU; 16 GB RAM comfortable; ~12 GB disk | unchanged | — |
| NFR-14 | **Restart resilience** | buttons persist; in-flight captures are lost (bot shows Retry) | acceptable for self-host (ADR-0004) | Nick |
| NFR-15 | **Retention** | raw HTML of failed fetches and the JSONL are kept indefinitely | policy + deletion command (Sprint 8) | Nick |

---

## 5. Data requirements
- **D-1** The JSONL sink is the source of truth; the vector store must be rebuildable from it.
- **D-2** Vectors are written and queried with one embedding model (`config.embed_model`); a mismatch is a test failure (`test_recommend_service_embeds_with_the_catalog_model`).
- **D-3** The label corpus (`fixtures/labels.jsonl`) records the exact input the extractor saw, so re-scoring never needs a re-fetch. Label provenance (`labeled_by`) is to be added (see `ML_ADVISOR_BRIEF.md`).
- **D-4** Secrets (`DISCORD_TOKEN`, `IG_*`, `data/ig_session.json`) are never committed; `.gitignore` enforces it.

---

## 6. Acceptance
A release candidate passes when: the offline suite is green in CI; the eval scorecard's
`--split train` floors hold; NFR-1 and NFR-2 have been re-measured and recorded in
`docs/BASELINE.md`; and one live end-to-end run (paste → card → 3 votes → Scheduled
Event → next-day prompt) has been performed and logged.

---

## 7. Traceability

| Requirement | Module | Test |
|---|---|---|
| FR-C1, C5 | `app/bot.py::on_message` | `app/test_bot.py` |
| FR-C2, C4, W2 | `serving/admin.py` | `test_serving_extra.py` |
| FR-C7 | `browser/ig_embed.py`, `sources/ig_authed.py`, `extractors/tiktok.py` | `test_ig_embed.py`, `test_ig_authed.py`, `test_tiktok.py` |
| FR-X1, NFR-3..6 | `pipeline/normalizer.py`, `eval.py` | `test_extraction_labels.py`, `eval_diagnostics.py` |
| FR-X2, X3 | `pipeline/transcriber.py`, `summarizer.py` | `test_transcriber.py`, `app/test_cards.py` |
| FR-X4, X7, NFR-9 | `pipeline/validator.py`, `temporal_resolver.py`, `orchestrator.py` | `test_ingestion.py` |
| FR-V1, V2, V7, V8, V9 | `app/cards.py`, `admin.py::_apply_vote` | `app/test_cards.py`, `test_serving_extra.py` |
| FR-V3 | `admin.py::followups/went`, `bot.py::followup_loop` | `test_went.py` |
| FR-V4, V5 | `serving/recommender.py`, `serving/app.py` | `test_ml_layers.py`, `test_serving.py` |
| D-2 | `serving/app.py` | `test_ingestion.py::test_recommend_service_embeds_with_the_catalog_model` |

---

## 8. Open questions for the advisor
1. Is there a required SRS template or section list for the capstone? This document
   follows IEEE 830's shape loosely and will be reformatted if needed.
2. NFR-3: is "accuracy stated against the extractive ceiling, with intervals" an
   acceptable headline, or is a single absolute target expected?
3. Does the usability study behind NFR-2's user-facing targets need IRB review?
