# Extraction Accuracy — the Label Loop

Written 2026-09-08. Owner: Nick.

## The problem

Capture "works" (the pipeline runs end to end) but the *fields it fills* are
often wrong. Snapshot of the live catalog on 2026-09-08: **12 of 18 spots had
`venue_name = "Locations"`** — a string scraped off Instagram's page chrome, not
a place. Others save the container ("Disneyland") instead of the tenant
("Bengal Barbecue"), or a decoy city, or a promo post that should have been
rejected.

Root causes are spread across stages:

| Stage | Failure seen |
|---|---|
| scrape (`text_isolation.py`) | selector `a[href*="/explore/locations/"]` matches IG's footer "Locations" link → `location_text = "Locations"` |
| normalize (`normalizer.py:91`) | no venue found → falls back to `raw_location_text` → `venue_name = "Locations"` |
| normalize (`_AT_VENUE`) | `at X` grabs the complex; no `from X` / `@handle` / first-person rule |
| geo (`geo_enricher.py`) | bare venue with no city won't resolve; broad city fallback shown as if precise |
| LLM stage | didn't run at all on the 2026-09-04 batch (`extractor = instagram/0.1`, no `+llm`) — failed silently |
| retention | only the caption's first line is kept; full caption, og:title, @handle discarded → can't re-extract without a re-fetch |

## The approach — name: **the label loop**

We do **not** add external services (no Google/Foursquare Places, no cloud
vision). Everything runs on the existing stack: local Ollama, Nominatim (already
a dependency), ChromaDB + the local embedder, plus in-house lookup tables.

Two halves:

### 1. Slot-first extraction

Stop handing the model a blob and trusting the JSON back. Food-reel captions
have a regular shape:

```
hook · venue-mention · location-phrase · dish · time · #tags
```

A deterministic parser fills **named slots** from spans in the text:

| Slot | Signal |
|---|---|
| `venue.handle` | `@mention`, weighted up after `from` / `at` / `by` / `owner of`; the poster's own handle when the caption is first-person ("our menu", "we're open") |
| `venue.from` | `from X` / `by X` where X is Title-Case |
| `venue.at` | `at X` — **demoted** when X is a known container (Disneyland, Universal, "the mall", airport codes) |
| `venue.quoted` | `"Quoted Name"` — usually a pop-up or dish |
| `location.pin` | `📍 …` line |
| `location.in` | `in <City>` |
| `location.hashtag` | `#<area>` matched against the in-house area gazetteer |

The **local LLM only adjudicates** ambiguous slots ("is `@waterfallchicken` a
venue or a person?") and normalizes casing. It is not the primary extractor.

**Confidence = which slot filled the field.** poster-handle + first-person =
high; `from X` = medium; title-fallback = low. Below a threshold the card asks
the group to confirm instead of guessing.

### 2. The labeled corpus — `fixtures/labels.jsonl`

One file, three jobs: **regression suite**, **accuracy scorecard**, **few-shot
bank**. One row per reviewed capture:

```json
{
  "url": "...",
  "input":     { "caption": "...", "og_title": "...", "handle": "...",
                 "ocr": "", "transcript": "", "hashtags": [] },
  "predicted": { "venue": "Locations", "city": null, "category": "food_drink" },
  "gold":      { "venue": "Erewhon", "city": "Los Angeles",
                 "category": "food_drink", "in_catalog": true },
  "verdict":   { "venue": "wrong", "city": "missing", "category": "right" },
  "note": "venue is the @handle after 'from'",
  "input_fidelity": "first_line_only",  // seed rows: full caption not retained
  "needs_recapture": true
}
```

`verdict` values: `right` · `wrong` · `missing` · `needs_review`.

### The loop

1. Capture happens; the extractor also writes `predicted` + the input it saw.
2. On `/dash` → **Review tab**, a human marks each field `right`/`wrong`/`missing`
   and types the correction. Row is appended to `labels.jsonl`.
3. Corrections feed back:
   - **alias table** — `predicted.venue` → `gold.venue` deterministic override
   - **few-shot injection** — embed the incoming caption, pull the *k* nearest
     labeled rows, prepend them to the LLM prompt as worked examples
   - **rule mining** — repeated `note`s ("it's the @handle") → add the regex
4. `python -m src.ingestion.eval` scores the extractor over the whole corpus;
   CI fails a change that regresses the score.
5. Fine-tuning llama3.2 on `input → gold` pairs is a **later** option, only once
   the corpus is a few hundred rows.

## Metrics

Run over a held-out slice of `labels.jsonl`:

- **venue exact** (normalized) and **venue fuzzy** (token overlap ≥ 0.6)
- **city / geo** match (within ~5 km when coords exist)
- **category** accuracy
- **catalog-inclusion** precision/recall (did `is_vague` catch promo posts)
- breakdown by failure bucket

## Measured results (15-row seed corpus, `python -m src.ingestion.eval`)

| stage | venue exact | category | city in geo |
|---|---|---|---|
| heuristic seed baseline | 27% (3/11) | 71% (10/14) | 0/8 |
| + rule set 1 (`@handle` / `from X` / quoted-name / container-demote) | 64% | 71% | 1/8 |
| + rule set 2 (category keywords + area/container gazetteer) | 64% | **100%** | 7/8 |
| + in-house handle word-split (`handle_split.py`) | **73% (8/11)** | 100% | 7/8 |

**LLM (llama3.2, 3B Q4, this CPU) — cut from the capture path.** Measured:
- *override mode* (old contract): venue **55%**, category **50%** — actively worse
  (overrode "Waterfall Chicken" → "Carson", the decoy city).
- *gap-filler mode* (new contract — LLM only fills what heuristics left blank):
  venue 73%, category back to 100% once category-fill was removed too (the model
  turns a correct "other" on promo posts into a wrong specific label).
- Net: **contributes nothing, costs 30–56 s/call.** The gap-filler merge code
  stays (right shape for a future GPU/bigger model); the extractor is not wired
  in by default. Revisit with a better model or hardware.

**Geocode stage — isolated, fed `gold` venue/city:** city-only resolves 100%
(8/8), venue-only 78%, `"venue, city"` compound 67% (worse — drop it, go
`venue` → `city`). Geo is **not** the bottleneck; the extractor is.

## Hosted-LLM option — Haiku on free credits

There are ~$100 of Anthropic API credits available (account-wide; usable on any
model). At Haiku 4.5 extractor rates (~$0.002/capture, ~$0.0012 cached) that's
~50k+ captures — ~$5/yr at dogfood volume, ~$50/yr at real adoption, ~$120/yr at
the 100-Nights target. Far under the geocoding API (~$400-600/yr) or a hosted
GPU (~$2-3k/yr). **Check the credit expiry** — promo credits usually lapse.

Best first use: the **one-time experiment**, not production. Point `eval.py`'s
transport at Haiku, batch-run the corpus + a catalog re-extract, and see whether
Haiku actually beats the heuristic's 73/100/87 — for free, before wiring it in.

Caveat unchanged: sending caption text to a hosted API breaks the
`MILESTONES.md` "post text never leaves our infrastructure" line. That's a
policy call. Likely shape if adopted: hosted tier + low-confidence captures
only; self-hosters keep Ollama.

## Build order

1. **slice 1 (done):** label schema + loader, seed `labels.jsonl`, `eval.py`
   scorecard, `test_extraction_labels.py`.
2. **slice 3 (done):** slot-first parser rule sets 1–2 + `handle_split.py`;
   LLM demoted to gap-filler and cut from the path; geocode stage isolated.
3. **scraper fix (done):** `text_isolation.py` now requires a numeric location
   id in the href (drops IG's "Locations" footer link); `_CHROME_LOCATIONS`
   guard in `normalizer._geo_context` as backup. Regression test added.
4. **slice 2 (next):** `/dash` Review tab; capture writes `predicted` + the
   input it saw, so real captures grow the corpus past these 15.
5. **Haiku experiment:** `eval.py` transport → Haiku on the free credits; decide
   from the number.
6. **transcription experiment:** one-time `WHISPER_MODEL=tiny` on the ~4
   no-text-signal rows → `eval.py`; ship async-enrichment only if it recovers ≥3.
7. **slice 4/5:** few-shot + alias table; grow the area gazetteer from `/dash`
   corrections; geo query uses venue-context + home city.

## Protecting the extractor (and API spend) from spam

Threat: a Discord user (or a compromised token) floods pasted links → hundreds
of pipeline runs / LLM calls → burned credits + a polluted catalog. Defence, in
priority order (all in-house):

1. **Dedup before any work.** A reel already in the guild's catalog (by
   `content_hash`) returns the existing record — no fetch, no LLM. Extends the
   existing `_CAPTURE_LOG` / `rec_dedup_window_s`.
2. **Confidence gate on the LLM.** The slot parser already yields a confidence
   (which slot filled the venue). Only captures below a threshold ever reach the
   model — caps LLM spend to the hard minority by construction.
3. **Global daily budget cap.** A hard counter: N LLM calls/day across
   everything. On hit → heuristic-only + log. Absolute protection for the credit
   balance, independent of per-user limits.
4. **Per-user + per-guild rate limits.** Token bucket (in-memory or the `db`
   service): e.g. 10 captures/user/10 min, 60/guild/hour. Over → a friendly
   "slow down" reply, no pipeline run.
5. **Lock the admin API.** It binds `0.0.0.0:8010` (systemd unit). Bind
   `127.0.0.1` + reach it from the container via the docker bridge, or add a
   shared-secret header the bot sends and the admin app checks.
6. **Discord-side raid guard.** Ignore messages with > K links; optionally
   ignore links from accounts that joined the guild < N minutes ago.

## Backfill

Re-capture the 12 poisoned rows with the scraper fix + LLM + OCR + `tiny`
transcription on. The ~5 whose venue is in the retained first line
(Erewhon, Tiendita, Waterfall Chicken, …) are fixed immediately by re-running
extraction — no re-fetch.
