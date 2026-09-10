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

**Held-out numbers** (`python -m src.ingestion.eval --offline --split test`, 115
scored rows).

> ⚠️ **Corrected 2026-09-10.** The venue numbers first recorded here (49.6% exact /
> 51.3% fuzzy) were inflated. `build_aliases.py` built the alias table from the
> *whole* corpus, so each test row's own gold label was compiled into the extractor
> that scored it — 20 of 63 overrides came from test rows. `eval.py --split test`
> now defaults to the train-only table; the honest figures are below. Full write-up:
> `docs/ML_REVIEW_QUESTIONS.md`; reproduce with `python scripts/eval_diagnostics.py`.

| metric | test (train-only aliases) | was reported | train |
|---|---|---|---|
| venue exact | **37.4%** | ~~49.6%~~ | 50.0% |
| venue fuzzy | 40.9% | ~~51.3%~~ | 52.7% |
| category | **63.2%** | 63.2% | 70.0% |
| city | **73.1%** | 73.1% | 78.2% |
| promo-rejection recall | **23.5%** | 23.5% | 32.4% |
| promo-rejection *precision* | **50.0%** | not reported | 78.6% |

Read venue against its ceiling, not against 100%: the gold venue is a literal
substring of the stored input in only **78%** of test rows, so 37.4% is roughly
**half of what any extractive method could reach** — the headroom is in the rules,
not in the model. `eval_diagnostics.py` section 6 prints this.

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
   ⚠️ That target sits inside the current 95% CI ([54.2%, 71.4%], 17pts wide at
   n=117), and 63.2% is only 8.5 points above always-guessing `food_drink` (54.7%).
   27 of the 43 category errors are `→ other` — no keyword matched at all, which is
   table coverage, not a representation problem. Build centroids from **train only**.
2. **promo-rejection — 24% held-out.** `looks_vague()` can't tell a no-name food
   post from a real place. This is genuinely an LLM job, not regex. Leave it for
   the Haiku pass.
3. **venue — 37.4% held-out** (not 50%; see the correction above). Ceiling is
   gatekept + spoken-only reels — measured at 78%, and a chunk of the missing 22%
   is canonicalisation rather than absence (`@cafefrancala` is right there in the
   caption for gold `Cafe Franca LA`). In-house headroom
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

- Every parser change: tune against `--split train`; look at `--split test` rarely
  and deliberately. It has already absorbed six rounds of tuning decisions.
- After any `labels.jsonl` change: `python scripts/build_aliases.py` (writes **both**
  tables — shipped and train-only), then bump the floors in
  `test_extraction_labels.py` to the new **`--split train`** numerators. The ratchet
  no longer floors on `--split all`; that was what let test drift optimistic.
- `docs/EXTRACTION_ACCURACY.md` has the full round-by-round history if you need context.

## Also on this branch (not extraction)

`/browse` pagination in `app/bot.py` + `app/cards.py` — built, **not deployed**
(`sudo docker compose up -d --build discord-bot`).
