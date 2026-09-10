# Taking the extraction work to an ML advisor

Written 2026-09-10. Companion to `docs/EXTRACTION_ACCURACY.md` and `docs/HANDOFF.md`.

Everything here is reproducible in one command:

```bash
python -m src.ingestion.eval --offline --split test   # the scorecard as reported
python scripts/eval_diagnostics.py                    # what the scorecard leaves out
```

---

## Part 1 — findings to put in front of them first

These are measured, not opinions. Lead with them; they reframe every number in
`HANDOFF.md`.

### 1. The held-out venue number is contaminated — 49.6% is really 37.4%

`scripts/build_aliases.py` builds `fixtures/venue_aliases.json` from the **whole**
corpus, mapping `predicted -> gold` for every row whose venue verdict was `wrong`.
`normalizer.py:265` applies that table as a deterministic override. So a test row's
own gold label is compiled into the extractor that is then scored on it.

| | as reported | test-derived aliases removed |
|---|---|---|
| venue exact (test) | 49.6% | **37.4%** |
| venue fuzzy (test) | 51.3% | 40.9% |

20 of 63 aliases are justified only by a test row; 23 of 122 test rows are keyed
by one. The 12-point gap is memorisation, not extraction.

The alias table is still a legitimate *product* feature — user corrections should
stick. The bug is scoring it as if it generalised.

### 2. The test set is too small to see the gains being chased

95% Wilson intervals on the 122-row test split:

| metric | point | 95% CI | width |
|---|---|---|---|
| venue exact | 49.6% | [40.6%, 58.6%] | 18 pts |
| category | 63.2% | [54.2%, 71.4%] | 17 pts |
| city | 73.1% | [63.3%, 81.1%] | 18 pts |
| promo rejected | 23.5% | [9.6%, 47.3%] | 38 pts |

`HANDOFF.md` targets category "63% → ~78%". That target sits inside the current
interval. Round-5-vs-round-6 comparisons at this n cannot separate a real gain
from resampling noise.

### 3. Category accuracy barely beats "always guess food_drink"

Micro accuracy 63.2% against a majority-class baseline of **54.7%** — an 8.5-point
edge, well inside the 17-point interval. And 27 of the 43 category errors are
`-> other`, i.e. no keyword matched at all, not a genuine class confusion.
Corpus is 91% two classes (`food_drink` 218, `cafe_dessert` 178), with
`live_music` at n=1 and `nightlife` at n=8.

### 4. `is_vague` is reported as recall only, and its precision is 50%

The scorecard's "promo rejected 23.5%" is recall on the negative class. The
confusion matrix on the test split:

|  | predicted keep | predicted reject |
|---|---|---|
| **really a place** | 101 | **4** ← real places thrown away |
| **really promo** | 13 | 4 |

Precision 50%. Pushing recall up without watching this column means silently
dropping spots users pasted — the worst failure mode this product has.

### 5. The split does not separate groups

The 70/30 split is a hash of the URL. But 22 poster handles appear on both sides
(70 rows), and 5 gold venues do (10 rows). Same creator = same caption template,
so those rows are not independent draws.

### 6. Six rounds of tuning, one test set, and a CI ratchet on `--split all`

`test_extraction_labels.py` floors on `--split all`, which contains the test rows,
and the floors are re-bumped after every round. Each round's decision to keep or
drop a rule has been informed by test performance. That is adaptive overfitting —
the test number drifts optimistic in a way no single re-run reveals.

---

## Part 2 — questions worth their time

Ordered by how much the answer changes what gets built next. These are the ones
where the answer is a judgement call about method, scope, or standards — not
something to be settled by reading the repo.

### On measurement

1. **Given finding 1, what is the defensible way to keep the alias table as a
   product feature while reporting a number that generalises?** Options seem to
   be: build aliases from train only and report that; report both with the
   memorised slice broken out; or treat aliases as a separate "correction layer"
   scored on rows unseen at build time. Which would they accept in a write-up?

2. **At n=122 test, what is the smallest improvement worth acting on?** Is
   paired testing on the same rows (McNemar / bootstrap over row-level
   correctness) enough to call a 5-point venue gain real, or does the corpus
   need to reach a specific size first — and what size?

3. **Should the split be grouped by poster handle instead of hashed by URL?**
   That costs test-set size (70 rows are affected) at a corpus this small.
   Which error is worse here — the optimism from correlated rows, or the wider
   intervals from a smaller test set?

4. **How many more times can this test set be looked at before it is burned?**
   Is the right move a three-way train/dev/test split now, with test locked
   until the end of the project — and if so, is 122 rows enough to divide again?

5. **Which headline metric should the project report?** Venue exact-match is
   brutal ("Bengal Barbecue" vs "Bengal BBQ" scores 0); fuzzy at Jaccard ≥ 0.6
   was picked without justification. Is there a standard for this kind of entity
   extraction they would rather see — and should abstention be scored separately
   instead of folded into venue accuracy, as `eval.py:venue_hit` does now?

### On what to build next

6. **Is the round-6 plan defensible?** `HANDOFF.md` proposes an
   embedding-nearest-centroid classifier for category using `mxbai-embed-large`.
   With one class at n=1, two classes at 91% of mass, and centroids that would
   have to be built train-only, is this the right shape — or is a simple
   regularised classifier over the embeddings (or over the existing keyword
   features) the better use of 400 labels?

7. **Where is the real ceiling?** ~16 rows are "gatekept" — the venue name is not
   in the caption, transcript, or tag, only in comments. Is the right framing to
   report accuracy on the recoverable subset separately, and treat retrieval of
   the missing signal as a different problem?

8. **Is `is_vague` better cast as a calibrated confidence threshold than a
   classifier?** The parser already emits `venue_confidence`; the LLM gate uses a
   hand-picked 0.6 bar (`eval.py:LOWCONF_BAR`). Should the confidence be
   calibrated against the corpus and one threshold tuned per operating point,
   instead of maintaining two separate mechanisms?

### On the labels themselves

9. **A meaningful share of the 417 labels was produced by an LLM labeling agent
   (see `scripts/label_with_haiku.py` and the `HANDOFF.md` note), with a single
   human reviewer via `scripts/review.py` and no second pass.** How much of the
   corpus needs a second independent annotator before the numbers carry weight,
   and should agreement be reported?

10. **The planned Haiku experiment evaluates a Claude model against gold labels a
    Claude model helped write.** How badly does that bias the comparison, and what
    would make it clean — a human-only held-out slice, scored separately?

### On scope and standards

11. **Does a hand-written slot parser with a labeled corpus count as the ML
    contribution for this course, or is a trained model expected?** The measured
    result so far is that the local LLM made things *worse* (llama3.2 3B: venue
    55% vs heuristic 73% on the seed corpus) — is "we measured it and cut it"
    a result they would credit, or a gap?

12. **What would they want to see in a write-up of a negative result?** The
    LLM-hurts finding and the leakage finding are the two most interesting things
    here and neither is a feature.

---

## Part 3 — what to bring

Keep it to four things. Do not walk them through the product.

1. **`docs/HANDOFF.md`, the numbers table** — one page, states the honest-vs-all
   split and the round history. This is the context.
2. **Live terminal, two commands** — `python -m src.ingestion.eval --offline
   --split test`, then `python scripts/eval_diagnostics.py`. The second one prints
   findings 1–5 above, including the 49.6% → 37.4% drop, in about 40 lines. Being
   able to run it in front of them is worth more than any slide.
3. **`fixtures/labels.jsonl`, two or three rows on screen** — one clean row, one
   `needs_review` gatekept row, one promo row. The schema (`input` / `predicted` /
   `gold` / `verdict` / `note`) is the part an ML advisor will actually recognise,
   and it shows the input the extractor saw is stored, so re-scoring never needs a
   re-fetch.
4. **`docs/EXTRACTION_ACCURACY.md`, the round-by-round table** — shows each change
   was measured, and shows the LLM was cut on evidence rather than vibes.

Say up front that the leakage was found and fixed in reporting, not hidden — that
is the strongest thing in this write-up, and it is the kind of thing an advisor
will trust the rest of the work more for hearing first.

---

## Things not worth asking

The repo already answers these; asking spends their time badly.

- *Does the local LLM help?* Measured, twice. It does not, on this hardware.
- *Is geocoding the bottleneck?* Measured in isolation (`--stage geocode`,
  city-only 100%). It is not; the extractor is.
- *How big should the corpus be?* Ask question 2 instead — the useful version is
  about detectable effect size, not a round number.
