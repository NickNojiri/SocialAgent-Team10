"""Diagnostics the main scorecard doesn't show — run before any accuracy claim.

    python scripts/eval_diagnostics.py            # test split (the honest one)
    python scripts/eval_diagnostics.py --split all

`src.ingestion.eval` reports point estimates. This adds the four things that
decide whether those point estimates mean anything:

1. **Alias leakage** — `fixtures/venue_aliases.json` is built from the *whole*
   corpus (`build_aliases.py`), so test rows contribute `predicted -> gold`
   overrides keyed on their own gold label. Re-scores the test split with those
   test-derived aliases removed.
2. **Confidence intervals** (Wilson, 95%) — with n≈115 a 3-point round-over-round
   gain is inside the noise band.
3. **Baselines + per-class breakdown** for category — micro accuracy over a corpus
   that is 91% two classes flatters the parser; the majority-class rate is the
   number to beat.
4. **is_vague as a classifier** — the scorecard reports recall only. Precision
   says what rejecting more promo posts costs in real places thrown away.

Also reports group overlap across the train/test split (same venue / same poster
handle on both sides), which the URL-hash split does not control for.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    LABELS_PATH, _SCOREABLE_VERDICTS, _norm, load_labels, raw_from_input, split_of,
)

ALIASES = REPO / "fixtures" / "venue_aliases.json"


def _akey(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (centre - half, centre + half)


def ci_line(name: str, k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return (f"  {name:<26} {k:>3}/{n:<3} = {k / n:5.1%}   95% CI [{lo:5.1%}, {hi:5.1%}]"
            f"   width {100 * (hi - lo):.0f}pts")


# ── 1 · alias leakage ─────────────────────────────────────────────────────────


def alias_provenance(rows: list[dict]) -> tuple[dict, set[str]]:
    """Which corpus rows justify each alias key, and which keys train alone justifies."""
    aliases = json.loads(ALIASES.read_text())
    train_justified: set[str] = set()
    by_split: collections.Counter = collections.Counter()
    for r in rows:
        pred = (r.get("predicted") or {}).get("venue")
        gold = (r.get("gold") or {}).get("venue")
        if not (pred and gold) or r.get("verdict", {}).get("venue") != "wrong":
            continue
        k = _akey(pred)
        if aliases.get(k) != gold:
            continue
        by_split[split_of(r["url"])] += 1
        if split_of(r["url"]) == "train":
            train_justified.add(k)
    return aliases, train_justified


def rescore_without_test_aliases(rows: list[dict], test_rows: list[dict]) -> None:
    from src.ingestion import eval as ev

    aliases, train_only = alias_provenance(rows)
    leaked = {k: v for k, v in aliases.items() if k not in train_only}
    test_keys = {_akey((r.get("predicted") or {}).get("venue")) for r in test_rows}

    print("── 1 · alias-table leakage " + "─" * 44)
    print(f"  aliases in table                : {len(aliases)}")
    print(f"  justified only by a TEST row    : {len(leaked)}")
    print(f"  test rows keyed by an alias     : {len(test_keys & set(aliases))} / {len(test_rows)}")

    settings = _settings()
    before = ev.score(test_rows, use_llm=False, settings=settings)

    backup = ALIASES.read_text()
    try:
        ALIASES.write_text(json.dumps({k: aliases[k] for k in sorted(train_only)},
                                      indent=2, ensure_ascii=False) + "\n")
        _reload_normalizer()
        after = ev.score(test_rows, use_llm=False, settings=settings)
    finally:
        ALIASES.write_text(backup)
        _reload_normalizer()

    print()
    print(f"  {'metric':<26} {'as reported':>12} {'train-only aliases':>20} {'delta':>8}")
    for label, attr in (("venue exact", "venue_exact"), ("venue fuzzy", "venue_fuzzy")):
        b, a = getattr(before, attr), getattr(after, attr)
        den = before.venue_scored
        print(f"  {label:<26} {b / den:>11.1%} {a / den:>20.1%} {(a - b) / den:>+8.1%}")
    print("\n  ^ the gap is memorised test gold, not extraction skill.")


def _settings():
    from src.ingestion.config import IngestionSettings

    return IngestionSettings(ollama_url="http://localhost:11434", ollama_model="llama3.2")


def _reload_normalizer() -> None:
    """`_ALIASES` is loaded at import time — re-import so the swap takes effect."""
    import importlib

    from src.ingestion import eval as ev
    from src.ingestion.pipeline import normalizer

    importlib.reload(normalizer)
    importlib.reload(ev)


# ── 2-4 · intervals, baselines, is_vague ──────────────────────────────────────


def breakdown(rows: list[dict]) -> None:
    from src.ingestion.eval import score
    from src.ingestion.pipeline.normalizer import normalize

    t = score(rows, use_llm=False, settings=_settings())

    print("\n── 2 · how wide is the noise band " + "─" * 37)
    print(ci_line("venue exact", t.venue_exact, t.venue_scored))
    print(ci_line("category", t.cat_hit, t.cat_scored))
    print(ci_line("city in geo text", t.city_hit, t.city_scored))
    print(ci_line("promo rejected (recall)", t.vague_hit, t.vague_scored))
    print("\n  ^ a round-over-round gain smaller than the width is not evidence.")

    per_class: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    confusions: collections.Counter = collections.Counter()
    vague_cm: collections.Counter = collections.Counter()
    for r in rows:
        gold, verdict = r.get("gold", {}), r.get("verdict", {})
        cand = normalize(raw_from_input(r["url"], r.get("input", {})))
        pred_cat = getattr(cand.get("category"), "value", cand.get("category"))
        if verdict.get("category") in _SCOREABLE_VERDICTS and gold.get("category"):
            per_class[gold["category"]][1] += 1
            per_class[gold["category"]][0] += int(_norm(pred_cat) == _norm(gold["category"]))
            confusions[(gold["category"], pred_cat)] += 1
        if gold.get("in_catalog") is not None:
            vague_cm[(bool(gold["in_catalog"]), bool(cand.get("is_vague")))] += 1

    print("\n── 3 · category vs. a trivial baseline " + "─" * 32)
    total = sum(n for _, n in per_class.values())
    biggest = max((n for _, n in per_class.values()), default=0)
    for cls, (hit, n) in sorted(per_class.items(), key=lambda kv: -kv[1][1]):
        print(f"  {cls:<16} {hit:>3}/{n:<3} = {hit / n:5.0%}")
    print(f"  {'-' * 34}")
    print(f"  {'parser (micro)':<16} {sum(h for h, _ in per_class.values()):>3}/{total:<3} = "
          f"{sum(h for h, _ in per_class.values()) / total:5.1%}")
    print(f"  {'always-majority':<16} {biggest:>3}/{total:<3} = {biggest / total:5.1%}   <- the number to beat")
    macro = sum(h / n for h, n in per_class.values()) / len(per_class)
    print(f"  {'macro-average':<16}     {'':<3}   {macro:5.1%}   <- what imbalance hides")
    worst = [f"{a} -> {b}: {c}" for (a, b), c in confusions.most_common(6) if a != b]
    print(f"  top confusions   : {', '.join(worst)}")

    print("\n── 4 · is_vague as a classifier " + "─" * 39)
    tp, fn = vague_cm[(False, True)], vague_cm[(False, False)]
    fp, tn = vague_cm[(True, True)], vague_cm[(True, False)]
    print(f"  correctly rejected promo   (TP): {tp}")
    print(f"  promo let through          (FN): {fn}")
    print(f"  REAL PLACE wrongly rejected(FP): {fp}   <- never reported by the scorecard")
    print(f"  real place kept            (TN): {tn}")
    if tp + fn:
        print(f"  recall    : {tp / (tp + fn):.1%}   (this is the scorecard's 'promo rejected')")
    if tp + fp:
        print(f"  precision : {tp / (tp + fp):.1%}   (half of what it rejects is a real place)")


# ── 5 · group overlap across the split ────────────────────────────────────────


def group_overlap(rows: list[dict]) -> None:
    print("\n── 5 · what the URL-hash split does not separate " + "─" * 22)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["all", "train", "test"], default="test")
    args = ap.parse_args()

    rows = load_labels()
    scored = [r for r in rows if args.split == "all" or split_of(r["url"]) == args.split]
    print(f"corpus: {len(rows)} rows ({LABELS_PATH.name}), scoring split={args.split} ({len(scored)} rows)\n")

    if args.split == "test":
        rescore_without_test_aliases(rows, scored)
    breakdown(scored)
    group_overlap(rows)


if __name__ == "__main__":
    main()
