# Extraction Accuracy — Handoff (2026-09-09)

Pick up on your main machine after `git fetch && git checkout extraction-label-loop && git pull`.

## What this branch is

Fixing venue/city/category extraction from pasted Instagram reels — the "label loop"
approach in `docs/EXTRACTION_ACCURACY.md`: an in-house slot-first parser measured
against a hand-labeled corpus with a frozen train/test split. No LLM in the path
(measured: llama3.2 3B on CPU made it *worse*).

## State right now

**Corpus:** `fixtures/labels.jsonl` — 433 rows, **417 labeled**, 16 `needs_review`
(all genuinely gatekept — venue nowhere in caption/transcript/tag). Train/test
split is deterministic by URL hash (`eval.py::split_of`), ~70/30.

**Honest held-out numbers** (`python -m src.ingestion.eval --offline --split test`,
115 scored rows — train≈test, so this is real, not corpus-fitted):

| metric | test | all (417) |
|---|---|---|
| venue exact | **49.6%** | 49.9% |
| venue fuzzy | 51.3% | 52.3% |
| category | **63.2%** | 68.1% |
| city | **73.1%** | 76.8% |
| promo-rejection (is_vague) | **23.5%** | 29.4% |

**237 tests pass.** Nothing uncommitted. **NOTE:** the last ~7 commits (round 5,
the +231 label merge, the `review.py` improvements, this doc) are committed but
**not yet pushed** — run `git push` on the laptop first, then `git pull` on the
desktop.

## First thing to do on the desktop

```bash
git checkout extraction-label-loop && git pull
python -m venv venv && venv/bin/pip install -r requirements.txt   # if fresh
venv/bin/python -m pytest -k "not live" -q                        # expect 237 passed
venv/bin/python -m src.ingestion.eval --offline --split test      # the honest number
venv/bin/python -m src.ingestion.eval --offline --split all --table   # per-row detail
```

## Where the parser is weak (round 6 targets, in priority order)

1. **category — 63% held-out.** Keyword matching (`_CATEGORY_KEYWORDS` in
   `normalizer.py`) has plateaued. Fix: embedding-nearest classifier — embed the
   caption with the local `mxbai-embed-large` (Ollama), compare to per-category
   centroids built from the labeled corpus, pick nearest. Plausibly 63% → ~78%.
2. **promo-rejection — 24% held-out.** `looks_vague()` can't tell a no-name food
   post from a real place. This is genuinely an LLM job, not regex. Leave it for
   the Haiku pass.
3. **venue — 50%.** Ceiling is gatekept + spoken-only reels. In-house headroom
   is small (~50→55 with more alias-table growth from corrections). Past that
   needs the LLM.
4. **Gatekept reels** (name only in comments): add a comment-scrape step to the
   capture path — `instagrapi` can pull top ~10 comments; the name is usually
   there. New ladder: caption → transcript → OCR → comments → give up.
   Distinguish "no-name place" (ask the user) from "not a place" (reject).

## The Haiku experiment (do after any more labeling)

Not a waste — it's the clean test of "does a cheap LLM beat the honest ~50%?".
Needs a **funded Anthropic API account** (`console.anthropic.com` + a card — Pro
credits do NOT work for API). ~$1–2 for one pass over 417 rows.

- `scripts/label_with_haiku.py` exists (second-opinion extractor). Point it at the
  corpus, compare its venue/city/category/in_catalog to gold on `--split test`.
- Transcripts are already in `input.transcript` — Haiku reading caption + transcript
  + IG tag is where it recovers gatekept/spoken-only venues.
- Decision it answers: ship heuristic-only, or heuristic + LLM-for-the-hard-rows.

## Finish labeling

16 `needs_review` left + growing the corpus is always worth it (bigger test set =
firmer numbers, more gazetteer cities).

```bash
venv/bin/python scripts/review.py          # opens each reel (embed view), 3 prompts, resumable
```
Q1 venue: name / ENTER=accept guess / `x`=not a place / `l`=listicle / `?`=later / `q`=quit.
`review.py` rewrites the whole file on each save — **don't run it alongside any
other job that writes `fixtures/labels.jsonl`.**

Two known label fixes not yet applied (were mid-session, would've been clobbered):
- `DYh-C2FPiGS` → category should be `cafe_dessert`, venue "The First Take"
- `DZzvbTupPND` (milbit) → note should be `name_in_comments`

## Getting more reels

```bash
venv/bin/python scripts/pull_dm_reels.py --out reels.txt --per-thread 400    # needs IG_USERNAME/PASSWORD in .env
venv/bin/python scripts/seed_corpus.py reels.txt --skip-existing --transcribe --out fixtures/labels.batchN.jsonl
```
Then label the new rows (`review.py`) or spin a labeling agent like this session did.
`scripts/watch_dm_reels.py` + `touch data/dm_watch.on` = auto-ingest new DM reels.

## Key files

| file | what |
|---|---|
| `src/ingestion/pipeline/normalizer.py` | the slot-first parser — all the rules |
| `src/ingestion/pipeline/handle_split.py` | `@bjsrestaurants` → "Bjs Restaurants" |
| `src/ingestion/eval.py` | `python -m src.ingestion.eval --offline --split {all,train,test} [--table]` |
| `fixtures/labels.jsonl` | the corpus (gold) |
| `fixtures/venue_aliases.json` | learned corrections; `scripts/build_aliases.py` regenerates from corpus |
| `test_extraction_labels.py` | CI ratchet — floors on `--split all` |
| `docs/EXTRACTION_ACCURACY.md` | the approach + round history |

## Rules of the road

- Every parser change: `eval.py --split test` before/after. Tune on train, report test.
- After any `labels.jsonl` change: `python scripts/build_aliases.py`, then bump the
  floors in `test_extraction_labels.py` to the new `--split all` numerators.
- `docs/EXTRACTION_ACCURACY.md` has the full round-by-round history if you need context.

## Also on this branch (not extraction)

`/browse` pagination in `app/bot.py` + `app/cards.py` — built, **not deployed**
(`sudo docker compose up -d --build discord-bot`).
