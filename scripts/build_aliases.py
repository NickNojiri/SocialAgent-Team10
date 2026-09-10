"""Regenerate the venue alias tables from the labeled corpus.

Every corpus row where the extractor produced a venue but the human gold differs
becomes a deterministic override: normalize(predicted) -> gold. This is how
/review.py corrections and every labeling pass compound — run this after any
change to fixtures/labels.jsonl.

    python scripts/build_aliases.py          # writes BOTH tables (do this)

Two tables, because one file cannot serve both jobs:

  venue_aliases.json        every labeled row  — what the product ships. A user's
                            correction should stick, whichever split it fell in.
  venue_aliases.train.json  train rows only    — what `eval.py` scores the test
                            split with. Building from the whole corpus compiles a
                            test row's own gold label into the extractor that
                            scores it; that was worth 12 points of fake venue
                            accuracy (docs/ML_REVIEW_QUESTIONS.md, finding 1).

    python scripts/build_aliases.py --split train --out /tmp/t.json   # one table
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
CORPUS = REPO / "fixtures" / "labels.jsonl"
OUT = REPO / "fixtures" / "venue_aliases.json"

from src.ingestion.eval import split_of  # noqa: E402
from src.ingestion.pipeline.normalizer import (  # noqa: E402
    _AREA_CITY_SET, _CONTAINERS, _REGION_STOP,
)

TRAIN_OUT = REPO / "fixtures" / "venue_aliases.train.json"

# A key that is really a place / chrome word must never become a global override.
_BAD_KEYS = {re.sub(r"[^a-z0-9]", "", s) for s in
             (_AREA_CITY_SET | _REGION_STOP | _CONTAINERS)} | {"locations", "location", "explore"}


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _lcs(a: str, b: str) -> int:
    """Longest common substring length — a spelling variant shares a real run;
    a wrong-location prediction ('newportbeach' -> 'Balboa Island Bakery') doesn't."""
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def build(rows: list[dict], split: str) -> dict[str, str]:
    """`split`: 'all' | 'train' | 'test' — which corpus rows may contribute an override."""
    aliases: dict[str, str] = {}
    for r in rows:
        if split != "all" and split_of(r["url"]) != split:
            continue
        pred = (r.get("predicted") or {}).get("venue")
        gold = (r.get("gold") or {}).get("venue")
        if not pred or not gold:
            continue
        if r.get("verdict", {}).get("venue") not in ("wrong",):
            continue
        k, gn = norm(pred), norm(gold)
        if len(k) < 5 or k == gn or k in _BAD_KEYS:
            continue
        if _lcs(k, gn) < 4:                    # not a spelling variant — a wrong place
            continue
        aliases[k] = gold                      # last write wins; corpus is small
    return aliases


def write(aliases: dict[str, str], out: Path) -> None:
    out.write_text(json.dumps(dict(sorted(aliases.items())), indent=2, ensure_ascii=False) + "\n")
    print(f"{len(aliases):>3} aliases -> {out.relative_to(REPO)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["all", "train", "test"],
                    help="build one table from this split (default: build both shipped tables)")
    ap.add_argument("--out", type=Path, help="write to this path instead of the default")
    args = ap.parse_args()

    rows = [json.loads(l) for l in CORPUS.read_text().split("\n") if l.strip()]

    if args.split:
        write(build(rows, args.split), args.out or (OUT if args.split == "all" else TRAIN_OUT))
        return

    write(build(rows, "all"), OUT)
    write(build(rows, "train"), TRAIN_OUT)


if __name__ == "__main__":
    main()
