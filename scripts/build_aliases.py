"""Regenerate fixtures/venue_aliases.json from the labeled corpus.

Every corpus row where the extractor produced a venue but the human gold differs
becomes a deterministic override: normalize(predicted) -> gold. This is how
/review.py corrections and every labeling pass compound — run this after any
change to fixtures/labels.jsonl.

    python scripts/build_aliases.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
CORPUS = REPO / "fixtures" / "labels.jsonl"
OUT = REPO / "fixtures" / "venue_aliases.json"

from src.ingestion.pipeline.normalizer import (  # noqa: E402
    _AREA_CITY_SET, _CONTAINERS, _REGION_STOP,
)

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


def main() -> None:
    rows = [json.loads(l) for l in CORPUS.read_text().split("\n") if l.strip()]
    aliases: dict[str, str] = {}
    for r in rows:
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
    OUT.write_text(json.dumps(dict(sorted(aliases.items())), indent=2, ensure_ascii=False) + "\n")
    print(f"{len(aliases)} aliases -> {OUT}")


if __name__ == "__main__":
    main()
