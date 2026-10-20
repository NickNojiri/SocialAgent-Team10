# Data plan: splits, keeping the numbers honest, and the road to 10,000 reels

Track B owns the labels and the scorer; this page is the rule book everyone follows when
they touch either. Why it exists: with one train/test split and the test number printed
on every CI run, every keep-or-drop decision was quietly a look at the held-out set. That
is the "cheating problem". Nobody meant to cheat, but six rounds of it make the test number
optimistic.

## 1. The three splits

Frozen in `fixtures/splits.json` (2026-10-20) and read by `src.ingestion.eval.split_of`.

| Split | Reels now | What it's for | Rules |
|---|---|---|---|
| **train** | 335 | Writing rules, building alias tables, training models | Look at it as much as you like |
| **dev** | 113 | Choosing between two versions (is my change better?) | Score it, but don't read its reels one by one to write rules |
| **test** | 185 | The number we report, once per milestone | `--split test` needs `--final "why"`; every read is logged in `docs/results/TEST_LEDGER.md` |

- **Test is unchanged.** It is the same 185 reels `docs/BASELINE.md` reports, so the
  Sprint 1 → Sprint 10 comparison still holds.
- **Dev is grouped by creator.** It was carved out of the old train split, a quarter of the
  creators together with all their reels, so no creator is in both train and dev.
- **New reels join their creator.** A new reel from a creator we already have goes to that
  creator's split, and an unknown creator's reels go 60/20/20 by a hash of the handle.
  Nobody picks splits by hand.
- **Never regenerate `splits.json`.** `scripts/freeze_splits.py` refuses to overwrite it,
  because reshuffling the held-out reels is the easiest way to get a better number without
  a better extractor. `--check` verifies it still matches.

**Alias tables.** There are three, so no scored reel's own label is ever compiled into the
extractor that scores it:

| Table | Built from | Scores |
|---|---|---|
| `venue_aliases.json` | every reel | nothing; this is what the product ships |
| `venue_aliases.train.json` | train + dev | test ("refit on train + dev, then test once") |
| `venue_aliases.fit.json` | train | dev, and the CI floors |

### Two caveats to report alongside the numbers

1. **Dev is optimistic for now.** Its 113 reels were in the old train split, so rounds 1–5 of
   rule tuning saw them, and dev scores 65.1% same-place against test's 58.3%. Dev is still
   right for "is version B better than version A", because both versions get the same reels.
   It gets cleaner as new labels arrive, since 20% of new creators land in dev.
2. **50 of the 185 test reels (27%) are from creators who also posted in train or dev.** That
   happened before the split grouped by creator. A rule that learned their style scores
   better on their test reels than it would on a stranger's. Report the stricter number
   too: `python -m src.ingestion.eval --offline --split test --unseen-creators --final "why"`.

### What changed in the tools

- CI and the AGENTS.md check print **dev**, not test.
- `eval.py`, `compare_commits.py`, `baseline_report.py`, `eval_diagnostics.py`,
  `error_buckets.py` and `learning_curve.py` all refuse `test` without `--final`.
- `test_extraction_labels.py` floors count **train only** (re-based once on 2026-10-20 with
  no change to the extractor; they only go up from here).
- `test_splits.py` checks every promise on this page.
- `python experiments/fit_check.py` prints train and dev side by side with a verdict:
  overfitting (a real train-dev gap) or underfitting (train far under the ceiling).
- `python scripts/count_captures.py` counts captured reels not yet labeled on a laptop,
  without printing captions, links or server ids.

## 2. Gold and silver labels

There are two kinds of label:

- **Gold**: a person checked it. These are the only labels that may be **scored**, in dev
  and test.
- **Silver**: made by a rule or a source with no person checking each one. Examples: the
  venue's own business account posted it, the poster's handle names the venue, or a
  synthetic caption built around an OpenStreetMap name. Silver is for **training only**.
  `split_of` sends any row with `"source": "silver"` to train, so a silver row can never be
  scored.

Silver rows live in `data/` (gitignored), not in `fixtures/`. At thousands of rows they are
other people's captions, and the repo is public.

## 3. The road to 10,000 reels

Labeling 10,000 reels by hand is out of reach: at about a minute each it's 170 hours. So the
target splits like this:

| Kind | Target | Why that many | How |
|---|---|---|---|
| Gold test | **500** (185 today) | 95% interval ±4 points instead of ±7, so a 5-point gain is provable | The test-side share of new gold labels, plus a hand-picked TikTok batch |
| Gold dev | **500** (113 today) | The same, for comparing versions | The dev-side share of new gold labels |
| Gold train | ~1,000 (335 today) | What the rules and the small models learn from | Targeted labeling: promos, TikTok, thin categories (below) |
| **Silver train** | **8,000+** | Enough text for the ranker and for the SSL study (`experiments/ssl/`) to matter | Business Discovery, handle matches, venue lists, synthetic captions |

That's about **2,000 gold + 8,000 silver = 10,000**, and only the gold needs people.

**Labeling 1,400 more gold** by confirming the extractor's guess (`scripts/review.py`)
rather than typing labels from scratch: 4 people × 25 reels a day ≈ 14 working days. Aim it
where the corpus is thin, since 100 random labels moved venue accuracy by about 0 points
(`experiments/learning_curve.py`):

- not-a-place posts: 69 today, 18 in test. Target 300.
- TikTok: 0 today. Target 300.
- nightlife, live music, outdoors, community: 13 between them. Target 50 each.
- your blind 40-reel second-label sheet, for labeler agreement (BASELINE.md "Labels").

**Silver sources**, all within the platforms' terms (get the professor's OK on this list
before collecting):

1. Reels users paste into SpotBot, from servers that opt in (wording goes in `/privacy`).
2. Instagram **Business Discovery** (official Graph API): a venue's own posts. The venue is
   the account, so each caption arrives labeled.
3. **Handle matches**: the poster's handle contains the gold venue in about 21% of rows.
4. **OpenStreetMap / Foursquare Open Source Places** extracts for Southern California: a
   list of real venue names to check candidates against and to build synthetic captions from.

Not allowed, at any scale: crawling hashtags, Explore or accounts; bulk comment scraping;
rotating accounts or timing to avoid detection.

## 4. Order of work

1. ✅ Three-way split, test-read guard, ledger, train-only floors (this page).
2. Count the unlabeled reels already captured on each laptop, then label the targeted gold.
3. Build the SoCal venue list (OpenStreetMap, Foursquare open data).
4. Candidate-ranker experiment: train on train, choose on dev, and test once at the end.
5. Meta developer app for Business Discovery → silver captions → the SSL study.
