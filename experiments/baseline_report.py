"""Baseline report: the numbers every model in the study is compared on.

    python experiments/baseline_report.py              # B0 rules, current test split
    python experiments/baseline_report.py --split all  # sanity check over everything

Sections
  1. Split    — sizes, and how many creators leak between train and test today,
                plus the creator-grouped 70/15/15 split the ML study will use.
  2. Venue    — treated as entity extraction: precision / recall / F1, plus
                "same place" accuracy (LABELING_GUIDE §3). Wilson 95% intervals.
  3. Place-or-not — is_vague vs gold in_catalog: precision / recall / F1, and
                ROC AUC using venue_confidence as the "this is a real place" score.
  4. Venue confidence — ROC AUC of venue_confidence for "the venue is right"
                (does the confidence actually rank right answers above wrong ones?)
  5. Category — accuracy, macro-F1, per-class precision / recall / F1, confusion matrix.

Rows marked needs_review are left out. Read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    load_labels, raw_from_input, split_of, use_aliases, venue_same_place, wilson_interval,
)
from src.ingestion.pipeline.normalizer import normalize  # noqa: E402

CATS = ("food_drink", "cafe_dessert", "nightlife", "market_popup", "live_music", "outdoors", "community", "other")


def creator(row: dict) -> str:
    h = re.sub(r"[^a-z0-9]", "", ((row.get("input") or {}).get("handle") or "").lower())
    return h or row["url"]                      # no handle: the reel is its own group


def creator_split(row: dict) -> str:
    """70/15/15 by creator: every reel of one account lands in the same split."""
    b = int(hashlib.md5(creator(row).encode()).hexdigest()[:8], 16) % 100
    return "test" if b < 15 else "val" if b < 30 else "train"


def ci(k: int, n: int) -> str:
    if not n:
        return "  n/a"
    lo, hi = wilson_interval(k, n)
    return f"{100 * k / n:5.1f}% [{100 * lo:.1f}, {100 * hi:.1f}] ({k}/{n})"


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def auc(scores: list[float], labels: list[bool]) -> float | None:
    """ROC AUC = chance a random positive outscores a random negative (ties = ½)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["test", "train", "all"], default="test")
    args = ap.parse_args()

    use_aliases("train")                                     # never score test with its own labels
    rows = [r for r in load_labels() if r.get("gold") and r.get("verdict", {}).get("venue") != "needs_review"]

    # ── 1. split ──────────────────────────────────────────────────────────────
    now = Counter(split_of(r["url"]) for r in rows)
    sides = defaultdict(set)
    for r in rows:
        sides[creator(r)].add(split_of(r["url"]))
    leaking = sum(1 for s in sides.values() if len(s) > 1)
    grouped = Counter(creator_split(r) for r in rows)
    print(f"1. SPLIT  {len(rows)} labeled reels, {len(sides)} creators")
    print(f"   current (by URL):     train {now['train']}  test {now['test']}   "
          f"creators in both: {leaking}")
    print(f"   proposed (by creator): train {grouped['train']}  val {grouped['val']}  test {grouped['test']}   "
          f"creators in both: 0")

    rows = [r for r in rows if args.split == "all" or split_of(r["url"]) == args.split]
    tp = fp = fn = same = 0
    gate = Counter()
    gate_scores, gate_y, conf_scores, conf_y = [], [], [], []
    cat_pairs = []
    for r in rows:
        g = r["gold"]
        c = normalize(raw_from_input(r["url"], r.get("input") or {}))
        pv, gv = c.get("venue_name"), g.get("venue")
        ok = venue_same_place(pv, gv)
        same += ok
        if pv and gv and ok:
            tp += 1
        else:
            fp += bool(pv)
            fn += bool(gv)
        conf = float(c.get("venue_confidence") or 0.0)
        if pv:
            conf_scores.append(conf)
            conf_y.append(bool(gv) and ok)
        if g.get("in_catalog") is not None:
            is_place, said_place = bool(g["in_catalog"]), not c.get("is_vague")
            gate[(is_place, said_place)] += 1
            gate_scores.append(conf if said_place else 0.0)
            gate_y.append(is_place)
        pc = getattr(c.get("category"), "value", c.get("category"))
        if g.get("category"):
            cat_pairs.append((g["category"], pc))

    # ── 2. venue ──────────────────────────────────────────────────────────────
    p, rc, f1 = prf(tp, fp, fn)
    print(f"\n2. VENUE ({args.split}, {len(rows)} reels) — entity extraction")
    print(f"   precision {100*p:5.1f}%   recall {100*rc:5.1f}%   F1 {100*f1:5.1f}%   (TP {tp}  FP {fp}  FN {fn})")
    print(f"   same-place accuracy  {ci(same, len(rows))}")

    # ── 3. place-or-not gate ──────────────────────────────────────────────────
    gp, gr, gf = prf(gate[(True, True)], gate[(False, True)], gate[(True, False)])
    a = auc(gate_scores, gate_y)
    print("\n3. PLACE-OR-NOT  (positive = a real place)")
    print(f"   precision {100*gp:5.1f}%   recall {100*gr:5.1f}%   F1 {100*gf:5.1f}%   "
          f"ROC AUC {a:.3f}" if a is not None else "   AUC n/a")
    print(f"   confusion: real→kept {gate[(True, True)]}  real→rejected {gate[(True, False)]}  "
          f"promo→kept {gate[(False, True)]}  promo→rejected {gate[(False, False)]}")

    # ── 4. venue confidence ───────────────────────────────────────────────────
    a = auc(conf_scores, conf_y)
    print("\n4. VENUE CONFIDENCE  (does venue_confidence rank right answers above wrong?)")
    print(f"   ROC AUC {a:.3f}  over {len(conf_y)} predicted venues ({sum(conf_y)} right)" if a is not None else "   n/a")

    # ── 5. category ───────────────────────────────────────────────────────────
    acc = sum(g == p for g, p in cat_pairs)
    print(f"\n5. CATEGORY  accuracy {ci(acc, len(cat_pairs))}")
    f1s = []
    print(f"   {'class':<13}{'n':>4}{'prec':>8}{'recall':>8}{'F1':>7}")
    for k in CATS:
        n = sum(g == k for g, _ in cat_pairs)
        ktp = sum(g == k and p == k for g, p in cat_pairs)
        kfp = sum(g != k and p == k for g, p in cat_pairs)
        kp, kr, kf = prf(ktp, kfp, n - ktp)
        if n or kfp:
            f1s.append(kf)
            print(f"   {k:<13}{n:>4}{100*kp:>7.1f}%{100*kr:>7.1f}%{100*kf:>6.1f}%")
    print(f"   macro-F1 {100 * sum(f1s) / len(f1s):.1f}%  (every class counts equally, so rare classes matter)")
    seen = [k for k in CATS if any(k in pair for pair in cat_pairs)]
    print("\n   confusion (rows = gold, cols = predicted)")
    print("   " + " " * 13 + "".join(f"{k[:6]:>8}" for k in seen))
    for g in seen:
        print(f"   {g:<13}" + "".join(f"{sum(1 for a, b in cat_pairs if a == g and b == p):>8}" for p in seen))


if __name__ == "__main__":
    main()
