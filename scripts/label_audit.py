"""Audit fixtures/labels.jsonl for labels that break docs/LABELING_GUIDE.md.

Read-only: it reports, it never edits the corpus. Each finding names the rule it
breaks, so whoever fixes it (`scripts/review.py` or by hand) knows what to change.

    python scripts/label_audit.py            # summary + every finding
    python scripts/label_audit.py --summary  # counts only

Checks
  spelling    the same venue spelled more than one way across rows
              ('yamas teriyaki' and 'Yamas Teriyaki House')               guide §2
  place       the gold venue is a city, region or named complex
              ('Mountain View', 'Disneyland') — confirm it is meant        guide §2
  missing     the gold venue appears nowhere in the stored input; the row
              may need re-capturing (OCR, full caption) before it is fair   guide §5
  conflict    in_catalog=false but a venue is set, or the reverse          guide §4
  provenance  rows without a named labeler (kappa needs names)             guide §6
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    LABELS_PATH, UNKNOWN_LABELER, load_labels, split_of, venue_same_place,
)
from src.ingestion.pipeline.normalizer import _AREA_CITY_SET, _CONTAINERS, _REGION_STOP  # noqa: E402

_INPUT_FIELDS = ("caption", "og_title", "handle", "ocr", "transcript", "hashtags")
_PLACES = {re.sub(r"[^a-z0-9]", "", p.lower()) for p in (_AREA_CITY_SET | _REGION_STOP | _CONTAINERS)}


def _squash(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _code(row: dict) -> str:
    return row["url"].rstrip("/").split("/")[-1]


def _in_input(gold: str, inp: dict) -> bool:
    target = _squash(gold)
    text = " ".join(str(inp.get(f) or "") for f in _INPUT_FIELDS)
    return bool(target) and target in _squash(text)


def audit(rows: list[dict]) -> dict[str, list[str]]:
    """Findings per check, as human-readable lines."""
    found: dict[str, list[str]] = defaultdict(list)

    # spelling: group gold venues that are the same place but written differently
    groups: list[list[tuple[str, str]]] = []
    for row in rows:
        gold = (row.get("gold") or {}).get("venue")
        if not gold:
            continue
        for g in groups:
            if venue_same_place(gold, g[0][1]):
                g.append((_code(row), gold))
                break
        else:
            groups.append([(_code(row), gold)])
    for g in groups:
        spellings = sorted({name for _, name in g})
        if len(spellings) > 1:
            found["spelling"].append(
                f"{' | '.join(repr(s) for s in spellings)}  rows: {', '.join(c for c, _ in g)}")

    for row in rows:
        code, gold, inp = _code(row), row.get("gold") or {}, row.get("input") or {}
        venue, in_cat = gold.get("venue"), gold.get("in_catalog")
        where = f"{code} [{split_of(row['url'])}]"
        if venue and _squash(venue) in _PLACES:
            found["place"].append(f"{where} gold venue {venue!r} is a city/region/complex")
        if venue and not _in_input(venue, inp):
            fid = row.get("input_fidelity") or "?"
            found["missing"].append(f"{where} {venue!r} not in stored input (fidelity={fid})")
        if in_cat is False and venue:
            found["conflict"].append(f"{where} in_catalog=false but venue={venue!r}")
        if in_cat is True and not venue and not gold.get("city"):
            found["conflict"].append(f"{where} in_catalog=true but no venue and no city")
        if (row.get("labeler") or UNKNOWN_LABELER) == UNKNOWN_LABELER:
            found["provenance"].append(where)
    return dict(found)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", action="store_true", help="print counts only")
    ap.add_argument("--path", type=Path, default=LABELS_PATH)
    args = ap.parse_args()

    rows = [r for r in load_labels(args.path) if r.get("gold")]
    found = audit(rows)
    print(f"label audit: {len(rows)} labeled rows in {args.path.name}")
    for check in ("spelling", "place", "missing", "conflict", "provenance"):
        print(f"  {check:<11}: {len(found.get(check, []))}")
    if args.summary:
        return
    for check in ("spelling", "place", "missing", "conflict"):
        items = found.get(check, [])
        if items:
            print(f"\n── {check} ({len(items)}) " + "─" * 40)
            for line in items:
                print(f"  {line}")
    if found.get("provenance"):
        print(f"\n── provenance: {len(found['provenance'])} rows have no named labeler "
              f"(set 'labeler' when you know who labeled them; see LABELING_GUIDE §6)")


if __name__ == "__main__":
    main()
