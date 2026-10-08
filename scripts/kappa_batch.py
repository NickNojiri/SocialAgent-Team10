"""Blind double-labeling for labeler agreement (Cohen's kappa) — LABELING_GUIDE §6.

1. Export a fixed random slice as a blind sheet (no gold, no prediction):

       python scripts/kappa_batch.py export --n 40
       -> data/kappa_batch.csv   (data/ is gitignored; open it in Google Sheets/Excel)

2. A second teammate fills venue / city / category / in_catalog for every row,
   from the reel itself, without looking at labels.jsonl or the bot's card.

3. Merge it back as `second_label` on each row (gold is never touched):

       python scripts/kappa_batch.py import data/kappa_batch.csv --labeler valeria

4. `python -m src.ingestion.eval --offline --full-report` then prints kappa per field.
   Kappa also needs the FIRST labeler's name on those rows (`labeler`); pass
   `--primary-labeler NAME` on import only if you know who wrote those labels.

Import rewrites fixtures/labels.jsonl in place, like scripts/review.py — never run
it while anything else is writing the corpus.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import LABELS_PATH, UNKNOWN_LABELER, load_labels  # noqa: E402

DEFAULT_SHEET = REPO / "data" / "kappa_batch.csv"
FIELDS = ("venue", "city", "category", "in_catalog")
CATEGORIES = "food_drink, cafe_dessert, nightlife, market_popup, live_music, outdoors, community, other"


def _code(row: dict) -> str:
    return row["url"].rstrip("/").split("/")[-1]


def pick(rows: list[dict], n: int, seed: int) -> list[dict]:
    """A fixed random slice of labeled rows: same seed, same reels, every time."""
    labeled = [r for r in rows if r.get("gold") and not r.get("second_label")]
    return random.Random(seed).sample(labeled, min(n, len(labeled)))


def export(rows: list[dict], out: Path, n: int, seed: int) -> int:
    chosen = pick(rows, n, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "url", "caption_start", *FIELDS, "notes"])
        for r in chosen:
            caption = ((r.get("input") or {}).get("caption") or "").replace("\n", " ")[:300]
            w.writerow([_code(r), r["url"], caption, "", "", "", "", ""])
    return len(chosen)


def _parse_in_catalog(value: str) -> bool | None:
    v = value.strip().lower()
    if v in ("true", "yes", "y", "1"):
        return True
    if v in ("false", "no", "n", "0"):
        return False
    return None


def merge(rows: list[dict], sheet: Path, labeler: str, primary: str | None) -> int:
    """Attach each filled sheet row to its corpus row as an independent second label."""
    if not labeler or labeler == UNKNOWN_LABELER:
        raise SystemExit("--labeler must name the second labeler")
    by_code = {_code(r): r for r in rows}
    merged = 0
    with sheet.open(encoding="utf-8", newline="") as f:
        for line in csv.DictReader(f):
            row = by_code.get((line.get("code") or "").strip())
            if row is None or not any((line.get(k) or "").strip() for k in FIELDS):
                continue
            if primary and row.get("labeler") in (None, "", UNKNOWN_LABELER):
                row["labeler"] = primary
            if row.get("labeler") == labeler:
                raise SystemExit(f"{_code(row)}: the second labeler must not be the first labeler")
            row["second_label"] = {
                "labeler": labeler,
                "independent": True,
                "gold": {
                    "venue": (line.get("venue") or "").strip() or None,
                    "city": (line.get("city") or "").strip() or None,
                    "category": (line.get("category") or "").strip().lower() or None,
                    "in_catalog": _parse_in_catalog(line.get("in_catalog") or ""),
                },
            }
            merged += 1
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write a blind sheet for a second labeler")
    ex.add_argument("--n", type=int, default=40)
    ex.add_argument("--seed", type=int, default=491)
    ex.add_argument("--out", type=Path, default=DEFAULT_SHEET)
    im = sub.add_parser("import", help="merge a filled sheet back as second_label")
    im.add_argument("sheet", type=Path)
    im.add_argument("--labeler", required=True, help="who filled the sheet")
    im.add_argument("--primary-labeler", help="name for rows whose first labeler is unknown")
    args = ap.parse_args()

    rows = load_labels()
    if args.cmd == "export":
        n = export(rows, args.out, args.n, args.seed)
        print(f"{n} reels -> {args.out}  (categories: {CATEGORIES})")
        return
    n = merge(rows, args.sheet, args.labeler, args.primary_labeler)
    LABELS_PATH.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                           encoding="utf-8", newline="\n")
    print(f"merged {n} second labels into {LABELS_PATH.relative_to(REPO)}")


if __name__ == "__main__":
    main()
