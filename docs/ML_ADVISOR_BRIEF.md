# ML advisor meeting — brief

Prepared 2026-09-15 by Nick. Companion to `docs/ML_REVIEW_QUESTIONS.md` (the full
analysis and the fourteen questions). This is the two-page version to send ahead and to
work from in the room. **The meeting is not booked yet** — that is the first action.

Everything quoted here reproduces with two commands on `main`:

```bash
python -m src.ingestion.eval --offline --split test   # the honest scorecard
python scripts/eval_diagnostics.py                    # intervals, baselines, ceiling
```

---

## 1. The one-paragraph version

We extract the venue, category and city from pasted Instagram reels with a
hand-written slot parser, scored against 433 labeled reels (417 labeled, 16 pending)
on a frozen 70/30 URL-hash split. On 2026-09-10 we found our own held-out venue
number was inflated — test rows' gold labels had leaked into a correction table the
extractor applies — and corrected it from 49.6% to **37.4%**. The local LLM we
started with was measured to make extraction worse on CPU and was cut. The gold venue
is literally present in the input for only **78%** of test rows, so the parser is at
about half of what any extractive method can reach. We want an ML advisor's judgement
on **how to report**, **how to split**, and **how to make the labels trustworthy**
before the team spends a semester moving these numbers.

## 2. The numbers (held-out test split, train-only alias table, 2026-09-15)

| metric | n | point | 95% Wilson CI | width |
|---|---|---|---|---|
| venue exact | 115 | **37.4%** | [29.1, 46.5] | 17 pts |
| venue fuzzy (Jaccard ≥ 0.6) | 115 | 40.9% | — | — |
| category (8 classes) | 117 | **63.2%** | [54.2, 71.4] | 17 pts |
| city in geo text | 93 | **73.1%** | [63.3, 81.1] | 18 pts |
| promo rejected — recall | 17 | 23.5% | [9.6, 47.3] | 38 pts |
| promo rejected — precision | 8 | **50.0%** | — | 4 real places thrown away |

Context that changes how to read them:
- **Category baseline:** always-guess `food_drink` scores 54.7%; macro-average 63.8%.
  The corpus is 91% two classes; `live_music` has n = 1. 27 of 43 category errors are
  "→ other" (no keyword matched), not class confusion.
- **Extractive ceiling:** the gold venue is a substring of caption / handle / location
  tag / transcript / hashtags in 76 of 98 rows (78%). Transcripts alone carry it in 6%.
- **Split leakage:** 22 poster handles (70 rows) and 5 venues (10 rows) appear on both
  sides of the URL-hash split.
- **Tuning debt:** six rounds of rules were kept or dropped with `--split test`
  visible; the CI ratchet has since moved to `--split train`.

## 3. What changed since the Sept 10 write-up

- Findings 1, 4 and 6 are fixed in code: `eval.py --split test` scores with the
  train-only alias table, prints precision next to recall, and CI floors on `train`.
- Finding 2's table in the review doc was still quoting 49.6%; it now shows the
  corrected 37.4% [29.1, 46.5].
- **New finding 8 — label provenance is not recorded.** Rows carry
  `url / input / predicted / gold / verdict / note`, nothing about *who* labeled.
  Commit history shows batch 2 (123 rows, `0b862770`) and batch 3 (231 rows,
  `e4af3aad`) — about 354 of the 417 labels — were produced in Claude Code sessions
  with Nick as the single reviewer via `review.py`; the 15 seed rows and later
  corrections are the clearly human-authored ones. Agreement has never been measured.
  The "human-only held-out slice" that question 10 assumes does not exist yet.

## 4. Three decisions we want to leave with

**D1 — Reporting protocol.** We propose: headline = venue exact on the held-out
split with the train-only alias table; always shown with a Wilson interval; stated
against the 78% ceiling as well as against 100%; round-over-round claims backed by a
paired test on the same rows (McNemar on row-level correctness), not two point
estimates; the alias table reported separately as a "correction layer" so its product
value is visible without inflating generalisation. *Ask: is that the protocol they would
accept in a capstone report, and is exact-match the right headline for entity
extraction (vs. canonicalised match, vs. P/R/F1 with abstention scored separately)?*

**D2 — Split policy.** We propose: re-split **grouped by poster handle** so no creator
straddles train/test, accept the smaller effective test set, carve a dev slice out of
train for tuning, and **lock test** until the Sprint 10 evidence round. *Ask: at ~120
test rows is a three-way split defensible, and what is the smallest improvement worth
acting on at this n — is a paired test enough, or is there a corpus size we should
reach first?*

**D3 — Labels.** We propose: add a `labeled_by` field now; double-label a 60-row
slice independently (Nick + one teammate) and report Cohen's κ; run the planned Haiku
comparison **only on that human-double-labeled slice**, since otherwise a Claude model
is scored against labels a Claude model helped write. *Ask: how large a double-labeled
slice, and what κ, before the numbers carry weight for grading?*

## 5. Our provisional answers, question by question

Numbers follow `ML_REVIEW_QUESTIONS.md` (renumbered 1–14 in reading order).

| # | Question (short) | Our position | What we need from them |
|---|---|---|---|
| 1 | alias table vs. generalisation | train-only for the score; ship the full table; break out the memorised slice (23 of 122 test rows) | accept / amend |
| 2 | smallest gain worth acting on | paired McNemar per round; we will bring the discordant-pair counts for round 5 vs 6 | a threshold, or a target n |
| 3 | group the split by handle? | yes; correlated rows are the worse error | confirm |
| 4 | is the test set burned? | partly; lock it now, add dev | is 122 enough to lock |
| 5 | headline metric | exact + canonicalised match; score abstention separately (produced-a-venue P/R) | a standard they recognise |
| 6 | round-6 category classifier | regularised logistic regression over `mxbai` embeddings + keyword features, train-only, class-weighted; nearest-centroid as the baseline; merge n≤5 classes for evaluation; report macro-F1 | agree on shape and metric |
| 7 | where is the ceiling | report accuracy on the recoverable 78% separately; gatekept reels are a *retrieval* problem (comment scrape, Track A) | agree on framing |
| 8 | `is_vague` as a threshold | calibrate `venue_confidence` on the corpus, one threshold per operating point; keep list/recipe rules as hard rejections | is a calibration curve enough |
| 9 | label provenance / second annotator | D3 | size and κ bar |
| 10 | Haiku vs Claude-written gold | D3: human slice only, plus a disagreement analysis | confirm |
| 11 | representation- or information-limited | information-limited; spend on retrieval (comments, `author_name`, canonicalisation) before models | agree; accept ceiling-relative reporting |
| 12 | self-supervision worth it? | no; weak supervision from handles (21% carry the name) and Nominatim validity is the cheap version; no pseudo-labels into test | is that a write-up-worthy framing |
| 13 | does a measured parser count as ML? | the contribution is the evaluation method, a measured negative result, and one learned component (Q6 + Q8) | what the course expects |
| 14 | writing up a negative result | hypothesis · setup · measurement · effect size with CI · why | an example they like |

## 6. What to bring (four things, ~10 minutes)

1. `docs/HANDOFF.md` — the numbers table with the corrected column. One page.
2. A terminal: the two commands above, run live. `eval_diagnostics.py` prints the
   leakage, the intervals, the baselines, the confusion matrix, the overlap and the
   ceiling in about 60 lines.
3. `fixtures/labels.jsonl` — three rows on screen: one clean, one `needs_review`
   (gatekept), one promo. The schema is the part they will recognise.
4. `docs/EXTRACTION_ACCURACY.md` — the round-by-round table, showing each change
   was measured and the LLM was cut on evidence.

Lead with the leakage: found and corrected in reporting, not hidden.

## 7. Not worth their time

Whether the local LLM helps (measured twice — no, on this hardware); whether geocoding
is the bottleneck (measured in isolation — no); "how big should the corpus be" as a
round number (ask Q2 instead).

## 8. After the meeting

Write the decisions into `ML_REVIEW_QUESTIONS.md` as a dated "Decisions" section;
turn D1–D3 into Track B's Sprint 1–2 issues; book a 20-minute follow-up when
`EVAL_REPORT_v1.md` exists (Sprint 3).
