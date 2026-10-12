"""Diagnostics the main scorecard doesn't show — run before any accuracy claim.

    python scripts/eval_diagnostics.py            # test split (the honest one)
    python scripts/eval_diagnostics.py --split all

`src.ingestion.eval` reports point estimates. This adds the things that decide
whether those point estimates mean anything:

1. **Alias leakage** — what the whole-corpus alias table is worth on the test
   split versus the train-only table `eval.py` now defaults to.
2. **Confidence intervals** (Wilson, 95%) — at n≈115 a 3-point round-over-round
   gain is inside the noise band.
3. **Baselines + per-class breakdown** for category — micro accuracy over a corpus
   that is 91% two classes flatters the parser; the majority-class rate is the
   number to beat.
4. **is_vague precision**, per-class, with the confusion matrix behind it.
5. **Group overlap** across the split — same venue / same poster on both sides.
6. **The extractive ceiling** — how often the gold venue is literally present in
   the stored input at all. No parser, model, or prompt can exceed this; it is the
   number accuracy should be read against.
"""

from __future__ import annotations

import argparse
import collections
import math
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.eval import (  # noqa: E402
    LABELS_PATH, _SCOREABLE_VERDICTS, _norm, format_rate, load_labels,
    paired_delta_interval, raw_from_input, score, split_of, use_aliases,
)
from src.ingestion.pipeline.normalizer import normalize  # noqa: E402

SETTINGS = IngestionSettings()
RULE = "─"


def head(n: int, title: str) -> None:
    line = f"── {n} · {title} "
    print(f"\n{line}{RULE * max(0, 70 - len(line))}")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (centre - half, centre + half)


def ci_line(name: str, k: int, n: int) -> str:
    if not n:
        return f"  {name:<26}   n/a"
    lo, hi = wilson(k, n)
    return f"  {name:<26} {format_rate(k, n)}   width {100 * (hi - lo):.0f}pts"


# ── 1 · alias leakage ─────────────────────────────────────────────────────────


def alias_leakage(test_rows: list[dict]) -> None:
    head(1, "what the whole-corpus alias table is worth on test")
    n_all = use_aliases("all")
    before = score(test_rows, use_llm=False, settings=SETTINGS)
    n_train = use_aliases("train")
    after = score(test_rows, use_llm=False, settings=SETTINGS)

    print(f"  overrides: all-corpus table {n_all}, train-only table {n_train} "
          f"({n_all - n_train} exist only because a test row contributed them)\n")
    print(f"  {'metric':<26} {'all-corpus':>12} {'train-only':>12} {'delta':>9}")
    den = before.venue_scored
    for label, attr in (("venue exact", "venue_exact"), ("venue fuzzy", "venue_fuzzy")):
        b, a = getattr(before, attr), getattr(after, attr)
        outcome_key = "venue_fuzzy" if attr == "venue_fuzzy" else "venue"
        paired = [(old[outcome_key], new[outcome_key])
                  for old, new in zip(before.rows, after.rows)
                  if old[outcome_key] is not None and new[outcome_key] is not None]
        paired_before = [old is True for old, _ in paired]
        paired_after = [new is True for _, new in paired]
        interval = paired_delta_interval(paired_before, paired_after)
        delta = (a - b) / den if den else 0.0
        delta_ci = "n/a" if interval is None else f"[{interval[0]:+.1%}, {interval[1]:+.1%}]"
        print(f"  {label:<26} {format_rate(b, den)} | {format_rate(a, den)} "
              f"delta {delta:+.1%} (paired 95% CI {delta_ci})")
    print("\n  ^ the gap is memorised test gold, not extraction skill. `eval.py --split test`\n"
          "    now defaults to the train-only table, so its headline is the right-hand column.")


# ── 2-4 · intervals, baselines, is_vague ──────────────────────────────────────


def breakdown(rows: list[dict]) -> None:
    t = score(rows, use_llm=False, settings=SETTINGS)

    head(2, "how wide is the noise band")
    print(ci_line("venue exact", t.venue_exact, t.venue_scored))
    print(ci_line("category", t.cat_hit, t.cat_scored))
    print(ci_line("city in geo text", t.city_hit, t.city_scored))
    print(ci_line("promo rejected (recall)", t.vague_hit, t.vague_scored))
    print("\n  ^ a round-over-round gain smaller than the width is not evidence.")

    per_class: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    category_outcomes: dict[str, list[bool]] = collections.defaultdict(list)
    confusions: collections.Counter = collections.Counter()
    for r in rows:
        gold, verdict = r.get("gold", {}), r.get("verdict", {})
        cand = normalize(raw_from_input(r["url"], r.get("input", {})))
        pred_cat = getattr(cand.get("category"), "value", cand.get("category"))
        if verdict.get("category") in _SCOREABLE_VERDICTS and gold.get("category"):
            hit = _norm(pred_cat) == _norm(gold["category"])
            per_class[gold["category"]][1] += 1
            per_class[gold["category"]][0] += int(hit)
            category_outcomes[gold["category"]].append(hit)
            confusions[(gold["category"], pred_cat)] += 1

    head(3, "category vs. a trivial baseline")
    total = sum(n for _, n in per_class.values())
    biggest = max((n for _, n in per_class.values()), default=0)
    for cls, (hit, n) in sorted(per_class.items(), key=lambda kv: -kv[1][1]):
        print(f"  {cls:<16} {format_rate(hit, n)}")
    print(f"  {'-' * 34}")
    hits = sum(h for h, _ in per_class.values())
    print(f"  {'parser (micro)':<16} {format_rate(hits, total)}")
    print(f"  {'always-majority':<16} {format_rate(biggest, total)}   <- the number to beat")
    macro = sum(h / n for h, n in per_class.values()) / len(per_class)
    rng = random.Random(0)
    macro_samples = sorted(
        sum(
            sum(rng.choices(outcomes, k=len(outcomes))) / len(outcomes)
            for outcomes in category_outcomes.values()
        ) / len(category_outcomes)
        for _ in range(2000)
    )
    print(f"  {'macro-average':<16} {macro:5.1%} (95% stratified bootstrap CI "
          f"[{macro_samples[49]:5.1%}, {macro_samples[1950]:5.1%}])")
    worst = [f"{a} -> {b}: {c}" for (a, b), c in confusions.most_common(6) if a != b]
    print(f"  top confusions   : {', '.join(worst)}")

    head(4, "is_vague as a classifier")
    tp, fp, fn = t.vague_hit, t.vague_fp, t.vague_scored - t.vague_hit
    tn = t.vague_kept - t.vague_fp
    print(f"  correctly rejected promo    (TP): {tp}")
    print(f"  promo let through           (FN): {fn}")
    print(f"  REAL PLACE wrongly rejected (FP): {fp}   <- the cost of pushing recall up")
    print(f"  real place kept             (TN): {tn}")
    if tp + fn:
        print(f"  recall    : {format_rate(tp, tp + fn)}")
    if tp + fp:
        print(f"  precision : {format_rate(tp, tp + fp)}")


# ── 5 · group overlap ─────────────────────────────────────────────────────────


def group_overlap(rows: list[dict]) -> None:
    head(5, "what the URL-hash split does not separate")
    for field, get in (("gold venue", lambda r: (r.get("gold") or {}).get("venue")),
                       ("poster handle", lambda r: (r.get("input") or {}).get("handle"))):
        seen: dict = collections.defaultdict(set)
        for r in rows:
            if k := get(r):
                seen[k].add(split_of(r["url"]))
        straddling = {k for k, v in seen.items() if len(v) > 1}
        n = sum(1 for r in rows if get(r) in straddling)
        print(f"  {field:<15}: {len(straddling)} values on both sides; rows affected "
              f"{format_rate(n, len(rows))}")
    print("  ^ rows sharing a venue or a poster are not independent draws.")


# ── 6 · extractive ceiling ────────────────────────────────────────────────────

_FIELDS = ("caption", "transcript", "handle", "hashtags", "og_title", "location_text")


def _loose(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", " ", (s or "").lower()).strip()


def _tight(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _present(gold: str, inp: dict, field: str) -> bool:
    if field == "handle":                       # run-together, so compare compacted
        return bool(_tight(gold)) and _tight(gold) in _tight(inp.get("handle"))
    value = " ".join(inp.get("hashtags") or []) if field == "hashtags" else inp.get(field)
    return bool(_loose(gold)) and _loose(gold) in _loose(value)


def ceiling(rows: list[dict]) -> None:
    head(6, "the extractive ceiling — is the answer even in the input?")
    scored = [r for r in rows
              if (r.get("gold") or {}).get("venue")
              and r.get("verdict", {}).get("venue") in _SCOREABLE_VERDICTS]
    if not scored:
        print("  no scoreable rows with a gold venue")
        return

    hits = collections.Counter()
    nowhere = []
    for r in scored:
        gold, inp = r["gold"]["venue"], r.get("input", {})
        found = [f for f in _FIELDS if _present(gold, inp, f)]
        hits.update(found)
        if found:
            hits["ANY"] += 1
        else:
            nowhere.append(r)

    n = len(scored)
    print(f"  gold venue present as a literal substring of the stored input  (n={n})\n")
    for f in _FIELDS:
        print(f"    in {f:<14}: {format_rate(hits[f], n)}")
    print(f"    {'-' * 30}")
    print(f"    in ANY of them : {format_rate(hits['ANY'], n)}   <- no extractive method can beat this")
    print(f"    nowhere        : {format_rate(len(nowhere), n)}   <- canonicalisation, or a source we don't capture")

    t = score(rows, use_llm=False, settings=SETTINGS)
    if t.venue_scored and hits["ANY"]:
        print(f"\n  venue exact {format_rate(t.venue_exact, t.venue_scored)} against "
              f"a ceiling {format_rate(hits['ANY'], n)}.")

    print("\n  a few 'nowhere' rows — check whether each is really missing or just uncanonical:")
    for r in nowhere[:5]:
        inp = r.get("input", {})
        cap_txt = (inp.get("caption") or "").replace("\n", " ")[:54]
        print(f"    gold={r['gold']['venue']!r:<32} handle=@{inp.get('handle') or '':<20} {cap_txt!r}")


def run_report(rows: list[dict], split: str) -> None:
    scored = [r for r in rows if split == "all" or split_of(r["url"]) == split]
    print(f"diagnostics: {len(rows)} corpus rows ({LABELS_PATH.name}), split={split} ({len(scored)} rows)")
    try:
        if split == "test":
            alias_leakage(scored)
        use_aliases("train" if split == "test" else "all")
        print(f"\n(sections below use the {'train-only' if split == 'test' else 'all-corpus'} alias table)")
        breakdown(scored)
        group_overlap(rows)
        ceiling(scored)
    finally:
        use_aliases("all")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["all", "train", "test"], default="test")
    args = ap.parse_args()
    run_report(load_labels(), args.split)


if __name__ == "__main__":
    main()
