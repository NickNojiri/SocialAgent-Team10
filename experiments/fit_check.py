"""Are we overfitting or underfitting? Train and dev side by side, with a verdict.

    python experiments/fit_check.py

Overfitting: good on the reels the rules were written from (train), worse on new
creators (dev). The gap between the two says how much.
Underfitting: weak even on train. The ceiling says how much better it could be: the share
of reels whose gold venue appears somewhere in what we captured.

Both splits are scored with the train-only alias table, so no dev reel's own label helps
the extractor that scores it (docs/DATA_PLAN.md). It never reads test.
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.eval import load_labels, score, split_of, use_aliases, wilson_interval  # noqa: E402

GAP_POINTS = 5        # a train-dev gap smaller than this is noise at our sizes
CEILING_POINTS = 10   # train this far under the ceiling = the method is too weak
MIN_REELS = 30        # below this a rate is too noisy to judge
_FIELDS = ("caption", "handle", "author_name", "transcript", "ocr", "location_text", "og_title")


@dataclass
class Rate:
    hits: int
    n: int

    @property
    def p(self) -> float:
        return self.hits / self.n if self.n else 0.0

    def show(self) -> str:
        if not self.n:
            return "n/a"
        lo, hi = wilson_interval(self.hits, self.n)
        return f"{self.p:6.1%} [{lo:.0%}, {hi:.0%}] n={self.n}"


def gap_interval(train: Rate, dev: Rate) -> tuple[float, float, float]:
    """train − dev, with a 95% normal-approximation interval (two independent samples)."""
    d = train.p - dev.p
    se = math.sqrt(train.p * (1 - train.p) / max(train.n, 1) + dev.p * (1 - dev.p) / max(dev.n, 1))
    return d, d - 1.96 * se, d + 1.96 * se


def verdict(train: Rate, dev: Rate, ceiling: float | None = None) -> str:
    if train.n < MIN_REELS or dev.n < MIN_REELS:
        return "too few reels to judge"
    d, lo, _ = gap_interval(train, dev)
    notes = []
    if d * 100 >= GAP_POINTS and lo > 0:
        notes.append(f"OVERFITTING: train beats dev by {d:.0%} and the interval excludes 0")
    elif d * 100 >= GAP_POINTS:
        notes.append(f"possible overfitting: train beats dev by {d:.0%}, but within noise")
    if ceiling is not None and (ceiling - train.p) * 100 >= CEILING_POINTS:
        notes.append(f"UNDERFITTING: train is {ceiling - train.p:.0%} under the ceiling of {ceiling:.0%}")
    if notes:
        return "; ".join(notes)
    return "fits: no meaningful gap" + (", close to the ceiling" if ceiling is not None else " (no ceiling measured)")


def _sq(s: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def ceiling(rows: list[dict]) -> float | None:
    """Share of reels with a gold venue whose name appears in the captured text."""
    named = [r for r in rows if (r.get("gold") or {}).get("venue")]
    if not named:
        return None
    seen = sum(1 for r in named if _sq(r["gold"]["venue"])
               in _sq(" ".join(str((r.get("input") or {}).get(f) or "") for f in _FIELDS)))
    return seen / len(named)


def rates(rows: list[dict]) -> dict[str, Rate]:
    t = score(rows, use_llm=False, settings=IngestionSettings())
    return {
        "venue, same place": Rate(t.venue_same, t.venue_scored),
        "category": Rate(t.cat_hit, t.cat_scored),
        "city": Rate(t.city_hit, t.city_scored),
        "promo caught": Rate(t.vague_hit, t.vague_scored),
    }


def main() -> int:
    rows = load_labels()
    use_aliases("fit")
    train_rows = [r for r in rows if split_of(r) == "train"]
    dev_rows = [r for r in rows if split_of(r) == "dev"]
    train, dev = rates(train_rows), rates(dev_rows)
    venue_ceiling = ceiling(train_rows)
    print("fit check: train (learned from) vs dev (new creators), train-only alias table\n")
    print(f"  {'metric':<18}{'train':<28}{'dev':<28}{'gap (train - dev)'}")
    for name in train:
        d, lo, hi = gap_interval(train[name], dev[name])
        print(f"  {name:<18}{train[name].show():<28}{dev[name].show():<28}{d:+.1%} [{lo:+.0%}, {hi:+.0%}]")
    if venue_ceiling is not None:
        print(f"\n  ceiling: the gold venue appears in the captured text for {venue_ceiling:.1%} of train reels")
    print()
    for name in train:
        print(f"  {name:<18}{verdict(train[name], dev[name], venue_ceiling if name.startswith('venue') else None)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
