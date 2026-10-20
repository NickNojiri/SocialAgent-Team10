# Baseline — every number we report, frozen, with its interval and its command

The Sprint 1 deliverable (`docs/CAPSTONE_PLAN.md`). Due 2026-09-26, recorded 2026-10-14.
Sprint 10 re-runs these same commands on the same frozen test split, and the change between
the two runs is the capstone result. Every interval is a 95% Wilson interval; a change is
only claimed with a paired test on the same rows.

## What is frozen

| | |
|---|---|
| Test split | `eval.split_of()`: URL hash, 70/30. 185 reels (175 scored for venue, 180 for category). Tune on train only |
| Labels | `fixtures/labels.jsonl` as of `40d04e26` (633 reels) |
| Rules baseline | the extractor at `bbb74211` (2026-10-12, before the error-bucket tuning loop) |
| Now | the extractor at `abe20811` (tuning loop, round 5; unchanged since) |
| Scorer | `src/ingestion/eval.py`, LABELING_GUIDE §3; train-only alias table on test |

The baseline is a **commit**, not a number: if the test labels are corrected later, re-score
it on the new labels with `python experiments/compare_commits.py --before bbb74211`.

## Accuracy (Track B) — held-out test split

| Metric | Stored at capture (what users got) | Rules baseline `bbb74211` | Now `abe20811` |
|---|---|---|---|
| Venue, same place | 46.3% [39.1, 53.7] | 54.9% [47.5, 62.0] | **58.3% [50.9, 65.3]** |
| Venue, exact | 33.1% [26.6, 40.4] | 42.3% [35.2, 49.7] | 45.1% [38.0, 52.5] |
| Category | 70.6% [63.5, 76.7] | 83.3% [77.2, 88.1] | 82.2% [76.0, 87.1] |
| Promo rejected (recall, n=18) | — | 16.7% [5.8, 39.2] | 33.3% [16.3, 56.3] |
| Real place kept (n=167) | — | 96.4% [92.4, 98.3] | 96.4% [92.4, 98.3] |

Commands: `python -m src.ingestion.eval --offline --split test --compare-stored --final "why"`
(first and last columns) · `python experiments/compare_commits.py --before bbb74211 --split test
--final "why"` (middle column, paired). Since 2026-10-20 every test read needs `--final` and is
logged in `docs/results/TEST_LEDGER.md`; the test reels themselves did not change
(`fixtures/splits.json`, `docs/DATA_PLAN.md`).

**Is the change real?** Paired on the same rows:

| Change | Venue, same place | McNemar | Reading |
|---|---|---|---|
| Stored at capture → now | +12.0 pts, CI [+5.1, +19.4] | 12 worse, 33 better, p = 0.0025 | a real gain |
| Rules baseline → now, **test** | +3.4 pts, CI [−0.6, +8.0] | 5 worse, 11 better, p = 0.21 | **not significant** |
| Rules baseline → now, train | +7.1 pts, CI [+3.8, +10.4] | 11 worse, 41 better, p < 0.0001 | the rules partly fit the reels they were tuned on |

Category, rules baseline → now: −1.1 pts (2 rows worse), p = 0.50.

More views of "now" (`python experiments/baseline_report.py`; all 185 test reels, so the
denominators differ slightly from the scorecard):

| | |
|---|---|
| Venue as entity extraction | precision 65.4% · recall 56.7% · F1 60.8% (TP 89, FP 47, FN 68) |
| Place-or-not gate | precision 93.1% · recall 96.4% · F1 94.7% · ROC AUC 0.760 |
| Venue confidence | ROC AUC 0.538 — barely above chance: confidence doesn't rank right answers above wrong ones |
| Category | macro-F1 48.2%; always-majority baseline 60.0% accuracy (the number to beat) |
| Lists / recipes | 1 of 24 listed spots named · 3 of 7 recipes kept out of the catalog |
| Extractive ceiling | the gold venue appears in the captured text for 80.3% [73.3, 85.7] of reels as an exact substring, and 94.9% [90.3, 97.4] (149/157) ignoring spacing and punctuation (`@yuzusushi` = "Yuzu Sushi"). With the 18 promos, that caps venue same-place at 95.4% (167/175): the gap to the ceiling is extraction, not capture |

**Labels.** A labeler is named on 358/633 rows, 56.6% [52.7, 60.4] (claude 199, kimberly 90,
nick 69; 275 unknown). **Inter-labeler agreement: not measured yet** — no reel has a blind second
label. Kimberly's review confirmed 78 of 91 existing labels, 85.7% [77.1, 91.5], but she saw the
label first, so that is an upper bound, not a kappa. **Pending: Nick's blind sheet** — 40 random
reels first labeled by Kimberly (seed 491), so each pair is two people. Fill venue, city, category
and in_catalog from the reel without opening `labels.jsonl`, then
`python scripts/kappa_batch.py import <sheet> --labeler nick`; `eval --full-report` prints kappa.

## Capture (Track A)

| Metric | Value | Command |
|---|---|---|
| Logged-out caption, 19 hand-picked links (Oct 4) | 19/19, 100% [83.2, 100] | `pytest test_ig_live.py` (live; appendix below) |
| **Logged-out pass rate, ≥50 reels nobody picked** | **pending — Track A** | the 220 links from the Instagram export qualify; needs a live network |
| 200 reels pulled unpicked from the DMs (logged-in source, Oct 8): caption | 200/200, 100% [98.1, 100] | `python experiments/baseline_report.py` §8 |
| … location tag | 102/200, 51.0% [44.1, 57.8] | same |
| … transcript | 181/200, 90.5% [85.6, 93.8] | same |
| … a real place | 176/200, 88.0% [82.8, 91.8] | same |

The pull didn't log failed fetches, so the 200/200 is over the reels it saved, not a pass rate.

## Recommendations ("near X")

| Metric | Value | Command |
|---|---|---|
| "near Laguna Niguel", 4 requests: expected spots found | 3/12, 25.0% [8.9, 53.2] | `python experiments/location_eval.py --stand-in` |
| … results actually within 25 km | 5/12, 41.7% [19.3, 68.0] | same |

Stand-in retrieval (keyword matching), because the production embedder can't run offline. The
real-model run is pending on Nick's machine: `python experiments/location_eval.py`.

## Security (Track D)

| Metric | Value | Command |
|---|---|---|
| Attack suite | 66 attacks, 58 refused, 8 got through (all 8 Track A SSRF: browser, DNS, embed and video redirects) | `python scripts/bench_security_attacks.py` (in CI) |
| Cross-tenant authorization | 47/47 forged requests refused, 23/23 authentic accepted | `python scripts/bench_admin_authz.py` |

## Experience (Track C)

| Metric | Value | Command |
|---|---|---|
| **Time to card, p50 / p95** | **pending — Track C** | the bot already logs it (`TIME_TO_CARD_LOG`); after ≥20 real pastes, read `/api/stats` → `time_to_card` |
| **`/setup` on a fresh server, time to first card** | **pending — Track C** | stopwatch plus a recording |

## Platform (Nick)

| Metric | Value | Command |
|---|---|---|
| Offline suite | 856 passed, 8 xfailed (2026-10-14) | `python -m pytest -k "not live" -q` |
| Queue capacity | see `docs/PHASE1_EVIDENCE.md` (2026-09-26) | `python scripts/load_test_jobs.py --json` |
| **Capture timings** | **pending** — needs a real capture log | `python scripts/summarize_captures.py` |

## Appendix · Track A's live check of the 19 hand-picked links (2026-10-04)

This table measures whether the logged-out Track A capture path obtains a caption
from each Instagram URL. Run it on home Wi-Fi because the test contacts real
Instagram; do not run it in CI or a sandbox.

```bash
python -m pytest test_ig_live.py -v -s --tb=short
```

Record one of `ok`, `login wall`, `no video`, `timeout`, or `no venue` for each
URL, along with the approximate elapsed time. The first local run obtained a
caption for every URL; the timings below come only from its first
`test_ig_caption_pass_rate` sweep.

| # | Link | Result | Rough time | Notes |
|---:|---|---|---|---|
| 1 | https://www.instagram.com/reel/DZIPn-ppdU4/ | ok | 3.7s | caption obtained |
| 2 | https://www.instagram.com/reel/DZXT8n7p8ME/ | ok | 4.5s | caption obtained |
| 3 | https://www.instagram.com/p/DZtDNAokmUh/ | ok | 4.3s | caption obtained |
| 4 | https://www.instagram.com/reel/DZpsu1eowNm/ | ok | 5.7s | caption obtained |
| 5 | https://www.instagram.com/reel/DYSYFuXsgbH/ | ok | 4.9s | caption obtained |
| 6 | https://www.instagram.com/reel/DC4YSNHP10U/ | ok | 5.1s | caption obtained |
| 7 | https://www.instagram.com/reel/C907WpjPUp2/ | ok | 5.0s | caption obtained |
| 8 | https://www.instagram.com/reel/DY0LbwRyhto/ | ok | 5.0s | caption obtained |
| 9 | https://www.instagram.com/reel/DUfLG_mAUc5/ | ok | 4.9s | caption obtained |
| 10 | https://www.instagram.com/reel/DRbgCEMkRSs/ | ok | 5.0s | caption obtained |
| 11 | https://www.instagram.com/reel/DKAXPHFSmEr/ | ok | 5.2s | caption obtained |
| 12 | https://www.instagram.com/reel/C51pgTAycsF/ | ok | 5.1s | caption obtained |
| 13 | https://www.instagram.com/reel/DVIRTIYjvt6/ | ok | 4.2s | caption obtained |
| 14 | https://www.instagram.com/reel/DU3evm2Ewhn/ | ok | 5.8s | caption obtained; transcription-plan reel |
| 15 | https://www.instagram.com/reel/DZd_edNJKJI/ | ok | 4.9s | caption obtained; transcription-plan reel |
| 16 | https://www.instagram.com/reel/DWCIfM4jYwa/ | ok | 5.0s | caption obtained; transcription-plan reel |
| 17 | https://www.instagram.com/reel/DX0z0HRyRsg/ | ok | 5.0s | caption obtained; transcription-plan reel |
| 18 | https://www.instagram.com/reel/DZtagrnR1vA/ | ok | 4.9s | caption obtained; transcription-plan reel |
| 19 | https://www.instagram.com/reel/DE4ECPeRPbv/ | ok | 5.0s | caption obtained; transcription-plan reel |

The embed-fallback test's second sweep is not included in the baseline timings.

- Run date: 2026-10-04
- Network: not recorded in the captured output
- Authenticated path: skipped/unavailable
- Caption pass rate: 100% (19/19)
- Login walls: 0
- Embed fallback recoveries: 0 (fallback had nothing to recover)
- Pytest result: 1 passed, 2 skipped
