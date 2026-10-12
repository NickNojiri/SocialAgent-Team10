# Notes for Track B (Valeria): what changed in your area, and what's proposed

From Nick, 2026-10-12. Everything below is on branch `claude/socialagent-task-0-review-fjfksj`,
not on `main` yet. Nothing here is merged into your files without your review; the round-5
work will come to you as a PR. Read this first, then the PR.

## The numbers today (rules baseline, held-out test split, 185 reels)

| Task | Number |
|---|---|
| Venue, same place (LABELING_GUIDE §3) | 52.4% [45.3–59.5] |
| Venue precision / recall / F1 | 55.6% / 53.1% / 54.3% |
| Category accuracy / macro-F1 | 83.8% / 48.8% |
| Place-or-not precision / recall / F1, ROC AUC | 90.3% / 97.5% / 93.8%, 0.794 |
| `venue_confidence` ROC AUC (does confidence rank right above wrong?) | 0.654 |

Reproduce: `python experiments/baseline_report.py`. Venue looks lower than the 60% from round 5
because a human review of the test labels fixed 30 of 55 checked rows. Several old golds had copied
the extractor's own mistakes (handles like "Sushi Murasakioc", a country as the venue).

## What changed in your files

| File | Change | Why |
|---|---|---|
| `pipeline/normalizer.py` | Round 5: category by keyword votes with place types first; venue from 📍 pins, name lines and sentences; trims location tails | Category 63% → 82%; venue gains on held-out |
| `pipeline/handle_split.py` | Splits handles with one unknown part (`tuttobellegelato` → Tuttobelle Gelato) | Handle-derived names |
| `eval.py` | `venue_same_place()`: same place ignoring spacing, punctuation, location or business-type tails | "Phin Coffee" = "Phin Coffee Torrance" was scored wrong |
| `scripts/build_aliases.py` | Skips blogger accounts (one handle → many venues) | Stopped @lacoffeelist renaming every post |
| `fixtures/labels.jsonl` | 433 → 633 rows; human review of the test split; new fields `state`, `cuisine`, `dish`, `categories` (multi-label), `gold_source`, `labeler`, `needs_recapture` | Label quality and provenance |
| New: `docs/LABELING_GUIDE.md`, `scripts/{label_audit,kappa_batch,label_confidence}.py`, `experiments/baseline_report.py` | Labeling rules, audit, blind double-labeling (kappa), active-labeling queue, P/R/F1/AUC report | Your #4/#5/#17 overlap: reuse or replace as you see fit |

`test_extraction_labels.py` floors were raised to the new train numbers (floors only go up).

## Proposed: split by creator (needs your yes, since `eval.py` is yours)

Today `split_of()` hashes the reel code: about 70/30, but **37 creators have reels in both train and
test**, so a model can learn an account's habits and get credit on test. The proposal hashes the
creator handle instead, so an account lands in exactly one split, and adds a validation split:

| | Train | Validation | Test |
|---|---|---|---|
| Now (~630 reels) | 60% (~375) | 15% (~95) | 25% (~155) |
| At ~1,000 reels | 70% | 15% | 15–25% |

A draft is `creator_split()` in `experiments/baseline_report.py`. Rolling it out changes which rows
are in test, so every reported number gets re-run once and reported both ways (old split vs creator
split). Settings for any model are chosen on validation; test is read once per model.

## What this means for your Phase 2–3 items

- **#16 category classifier:** beat 83.8% accuracy *and* 48.8% macro-F1. Rare classes are tiny
  (nightlife 12, market_popup 17, live_music 1, community 0 across all 633), so per-class claims
  need more labels there first. Labels are now multi-label where it applies (`gold.categories`,
  e.g. Uchi = food_drink + nightlife). Train on the train split only.
- **#6 place-or-not gate:** 17 of 22 promos get through as places, and 4 real places are rejected.
  `VENUE_CONF` in `normalizer.py` is miscalibrated. Measured on train:
  `bare_mention` is right 88% of the time (hand-set 0.60), `at_venue` 44% (0.55), `pin` 72% (0.80),
  `none` 48% (0.0). `python scripts/label_confidence.py --table` prints the measured values.
- **#17 report generator:** `experiments/baseline_report.py` already prints the metrics; the PDF
  plan (figures + appendix) is with Nick.

## Your share of the label review

Rows 286–379 in `fixtures/review/queue_2026-10.csv` (owner = valeria), 94 reels. The copy-paste
prompt is in the team doc Nick sent. Your answers go in `fixtures/review/valeria.jsonl` on your own
branch `labels/review-valeria`, then a PR into `claude/socialagent-task-0-review-fjfksj`.

## Decisions we need from you

1. OK to switch `split_of()` to the creator split (with a validation split)?
2. OK to update `VENUE_CONF` to the measured values, or do you want to own that inside #6?
3. Anything in round 5 you want changed before it lands in your area?
