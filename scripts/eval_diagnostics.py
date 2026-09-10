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
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.eval import (  # noqa: E402
    LABELS_PATH, _SCOREABLE_VERDICTS, _norm, load_labels, raw_from_input, score,
    split_of, use_aliases,
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
    return (f"  {name:<26} {k:>3}/{n:<3} = {k / n:5.1%}   95% CI [{lo:5.1%}, {hi:5.1%}]"
            f"   width {100 * (hi - lo):.0f}pts")


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
        print(f"  {label:<26} {b / den:>11.1%} {a / den:>12.1%} {(a - b) / den:>+9.1%}")
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
    confusions: collections.Counter = collections.Counter()
    for r in rows:
        gold, verdict = r.get("gold", {}), r.get("verdict", {})
        cand = normalize(raw_from_input(r["url"], r.get("input", {})))
        pred_cat = getattr(cand.get("category"), "value", cand.get("category"))
        if verdict.get("category") in _SCOREABLE_VERDICTS and gold.get("category"):
            per_class[gold["category"]][1] += 1
            per_class[gold["category"]][0] += int(_norm(pred_cat) == _norm(gold["category"]))
            confusions[(gold["category"], pred_cat)] += 1

    head(3, "category vs. a trivial baseline")
    total = sum(n for _, n in per_class.values())
    biggest = max((n for _, n in per_class.values()), default=0)
    for cls, (hit, n) in sorted(per_class.items(), key=lambda kv: -kv[1][1]):
        print(f"  {cls:<16} {hit:>3}/{n:<3} = {hit / n:5.0%}")
    print(f"  {'-' * 34}")
    hits = sum(h for h, _ in per_class.values())
    print(f"  {'parser (micro)':<16} {hits:>3}/{total:<3} = {hits / total:5.1%}")
    print(f"  {'always-majority':<16} {biggest:>3}/{total:<3} = {biggest / total:5.1%}   <- the number to beat")
    macro = sum(h / n for h, n in per_class.values()) / len(per_class)
    print(f"  {'macro-average':<16} {'':>3} {'':<3}   {macro:5.1%}   <- what the imbalance hides")
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
        print(f"  recall    : {tp / (tp + fn):.1%}")
    if tp + fp:
        print(f"  precision : {tp / (tp + fp):.1%}")


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
        print(f"  {field:<15}: {len(straddling):>3} values on both sides of the split, {n} rows affected")
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
        print(f"    in {f:<14}: {hits[f]:>3}  ({hits[f] / n:4.0%})")
    print(f"    {'-' * 30}")
    print(f"    in ANY of them : {hits['ANY']:>3}  ({hits['ANY'] / n:4.0%})   <- no extractive method can beat this")
    print(f"    nowhere        : {len(nowhere):>3}  ({len(nowhere) / n:4.0%})   <- canonicalisation, or a source we don't capture")

    t = score(rows, use_llm=False, settings=SETTINGS)
    if t.venue_scored and hits["ANY"]:
        got = t.venue_exact / t.venue_scored
        cap = hits["ANY"] / n
        print(f"\n  venue exact {got:.1%} against a {cap:.0%} ceiling = {got / cap:.0%} of what is reachable;"
              f"\n  {cap - got:.0%} of the corpus is headroom the current rules leave on the table.")

    print("\n  a few 'nowhere' rows — check whether each is really missing or just uncanonical:")
    for r in nowhere[:5]:
        inp = r.get("input", {})
        cap_txt = (inp.get("caption") or "").replace("\n", " ")[:54]
        print(f"    gold={r['gold']['venue']!r:<32} handle=@{inp.get('handle') or '':<20} {cap_txt!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["all", "train", "test"], default="test")
    args = ap.parse_args()

    rows = load_labels()
    scored = [r for r in rows if args.split == "all" or split_of(r["url"]) == args.split]
    print(f"corpus: {len(rows)} rows ({LABELS_PATH.name}), scoring split={args.split} ({len(scored)} rows)")

    try:
        if args.split == "test":
            alias_leakage(scored)
        use_aliases("train" if args.split == "test" else "all")
        print(f"\n(sections below use the {'train-only' if args.split == 'test' else 'all-corpus'} alias table)")
        breakdown(scored)
        group_overlap(rows)
        ceiling(scored)
    finally:
        use_aliases("all")


if __name__ == "__main__":
    main()
