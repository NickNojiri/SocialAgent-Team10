"""Rank reels for labeling by how likely the extractor's venue is wrong — LABELING_GUIDE §6.

The extractor's `venue_confidence` is a hand-set number per slot. This script
replaces it with a measured one: for every slot, the share of TRAIN rows where
that slot's venue was the same place as gold (smoothed so a slot seen 3 times
can't claim 100%). Test rows are never used.

    python scripts/label_confidence.py fixtures/labels.new.jsonl
    -> data/label_queue.csv   lowest confidence first: label those, skim the rest

    python scripts/label_confidence.py --table   # print the measured table only

Read-only on its input; the queue goes to data/ (gitignored).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    load_labels, raw_from_input, split_of, use_aliases, venue_same_place,
)
from src.ingestion.pipeline.normalizer import normalize  # noqa: E402

DEFAULT_OUT = REPO / "data" / "label_queue.csv"


def calibrate(rows: list[dict]) -> dict[str, float]:
    """Slot -> smoothed share of train rows whose venue matched gold."""
    hits: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        if not r.get("gold") or split_of(r["url"]) != "train":
            continue
        cand = normalize(raw_from_input(r["url"], r.get("input") or {}))
        h = hits[cand.get("venue_slot") or "none"]
        h[0] += venue_same_place(cand.get("venue_name"), r["gold"].get("venue"))
        h[1] += 1
    # Laplace smoothing: (hits + 1) / (seen + 2) pulls rare slots toward 0.5
    return {slot: (h + 1) / (n + 2) for slot, (h, n) in hits.items()}


def rank(rows: list[dict], table: dict[str, float]) -> list[dict]:
    """One queue entry per row, least confident first."""
    out = []
    for r in rows:
        inp = r.get("input") or {}
        if not inp:
            continue                                    # fetch failed: nothing to score
        cand = normalize(raw_from_input(r["url"], inp))
        slot = cand.get("venue_slot") or "none"
        out.append({
            "url": r["url"],
            "confidence": round(table.get(slot, 0.5), 2),  # unseen slot: no evidence either way
            "slot": slot,
            "venue": cand.get("venue_name") or "",
            "caption_start": (inp.get("caption") or "").replace("\n", " ")[:120],
        })
    return sorted(out, key=lambda e: e["confidence"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows", type=Path, nargs="?", help="labels-shaped .jsonl to rank (e.g. from seed_corpus.py)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--table", action="store_true", help="print the measured slot table and stop")
    args = ap.parse_args()

    use_aliases("train")                                # never let test labels leak into the table
    table = calibrate(load_labels())
    if args.table or not args.rows:
        for slot, p in sorted(table.items(), key=lambda kv: kv[1]):
            print(f"  {slot:<15} {p:.2f}")
        return

    rows = [json.loads(l) for l in args.rows.read_text(encoding="utf-8").split("\n") if l.strip()]
    queue = rank(rows, table)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["url", "confidence", "slot", "venue", "caption_start"])
        w.writeheader()
        w.writerows(queue)
    low = sum(e["confidence"] < 0.7 for e in queue)
    print(f"{len(queue)} reels ranked -> {args.out}  ({low} below 0.70: label these first)")


if __name__ == "__main__":
    main()
