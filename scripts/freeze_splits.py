"""Freeze the train / dev / test split into fixtures/splits.json (run once).

    python scripts/freeze_splits.py            # writes the manifest if there is none
    python scripts/freeze_splits.py --check    # recomputes and says whether it still matches

Why a frozen file and not just a hash: test has to stay the same 185 reels that
BASELINE.md reports, while dev is new. So:

  test   the old held-out split, unchanged: a reel is test if its URL hash < 30.
  dev    carved out of the old train split by creator: about a quarter of the
         creators, with every reel they posted. Used to choose between versions.
  train  the rest. Rules, alias tables and models learn from these only.

New reels never need this script: `eval.split_of` puts a known creator's new reels
with that creator, and an unknown creator's 60/20/20 by hash.

There is no --force. Re-freezing reshuffles which reels are held out, which is the
easiest way to get a better test number without a better extractor. If the split
really has to change, delete the file in a commit that says why.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    SPLITS_PATH, _code, _hash100, creator_of, load_labels,
)

DEV_SHARE = 25          # percent of the old train split's creators that become dev
_PRIORITY = {"test": 0, "dev": 1, "train": 2}


def freeze(rows: list[dict]) -> dict:
    split_rows: dict[str, str] = {}
    for r in rows:
        if r.get("source") == "silver":
            continue                                   # never held out, never frozen
        code = _code(r["url"])
        if _hash100(code) < 30:                        # the original 70/30 rule
            split_rows[code] = "test"
        else:
            split_rows[code] = "dev" if _hash100(creator_of(r)) < DEV_SHARE else "train"
    creators: dict[str, str] = {}
    for r in rows:
        code = _code(r["url"])
        if code not in split_rows:
            continue
        c, s = creator_of(r), split_rows[code]
        if c not in creators or _PRIORITY[s] < _PRIORITY[creators[c]]:
            creators[c] = s
    return {
        "version": 2,
        "frozen": date.today().isoformat(),
        "note": "Do not edit by hand or regenerate; see scripts/freeze_splits.py.",
        "rows": dict(sorted(split_rows.items())),
        "creators": dict(sorted(creators.items())),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare the manifest with a fresh freeze")
    args = ap.parse_args(argv)
    fresh = freeze(load_labels())
    if args.check:
        if not SPLITS_PATH.exists():
            print("no manifest yet: run without --check")
            return 1
        old = json.loads(SPLITS_PATH.read_text(encoding="utf-8"))
        moved = [c for c, s in old["rows"].items() if fresh["rows"].get(c, s) != s]
        print(f"{len(old['rows'])} frozen reels, {len(moved)} would move" + (f": {moved[:10]}" if moved else ""))
        return 1 if moved else 0
    if SPLITS_PATH.exists():
        print(f"refused: {SPLITS_PATH.relative_to(REPO)} already exists, and re-freezing reshuffles "
              "the held-out reels. See this script's docstring.", file=sys.stderr)
        return 1
    SPLITS_PATH.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8", newline="\n")
    counts = Counter(fresh["rows"].values())
    print(f"froze {len(fresh['rows'])} reels -> {SPLITS_PATH.relative_to(REPO)}: "
          + ", ".join(f"{s} {counts[s]}" for s in ("train", "dev", "test")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
