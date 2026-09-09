"""Fast keyboard review of corpus rows that need a human.

    python scripts/review.py            # rows with verdict.venue == "needs_review"
    python scripts/review.py --misses   # + rows the extractor currently gets wrong
    python scripts/review.py --no-open  # don't launch the browser

For each row it opens the reel in your browser and prompts. Progress is written
after every row, so Ctrl-C any time and re-run to resume.

Prompts (Enter = keep what's shown):
  venue :  text = set it · "-" = not a real place (in_catalog=false) · Enter = accept predicted
  city  :  text = set it · "-" = clear · Enter = accept predicted
  cat   :  1-8 from the menu · Enter = keep
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "fixtures" / "labels.jsonl"
CATS = ["food_drink", "cafe_dessert", "nightlife", "live_music",
        "market_popup", "outdoors", "community", "other"]


def _load() -> list[dict]:
    return [json.loads(l) for l in CORPUS.read_text().split("\n") if l.strip()]


def _save(rows: list[dict]) -> None:
    CORPUS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--misses", action="store_true", help="also review extractor-wrong rows")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    rows = _load()
    todo = []
    for idx, r in enumerate(rows):
        v = r.get("verdict", {})
        if v.get("venue") == "needs_review":
            todo.append(idx)
        elif args.misses and v.get("venue") in ("wrong", "missing"):
            todo.append(idx)

    print(f"{len(todo)} rows to review  ({CORPUS})\n", file=sys.stderr)
    for n, idx in enumerate(todo, 1):
        r = rows[idx]
        code = r["url"].rstrip("/").split("/")[-1]
        i = r.get("input", {})
        p = r.get("predicted", {})
        g = r.get("gold", {})
        cap = " ".join((i.get("caption") or "").split())

        print("\n" + "═" * 70)
        print(f"[{n}/{len(todo)}]  {code}   @{i.get('handle')}   {r['url']}")
        if i.get("venue_candidate"):
            print(f"  IG tag  : {i['venue_candidate']!r}")
        print(f"  predicted: venue={p.get('venue')!r}  city={p.get('city')!r}  cat={p.get('category')}")
        print(f"  caption : {cap[:500]}")
        if not args.no_open:
            subprocess.Popen(["xdg-open", r["url"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        try:
            ans = input(f"  venue [{p.get('venue') or ''}] (- = not a place, s = skip, q = quit): ").strip()
        except EOFError:
            break
        if ans == "q":
            break
        if ans == "s":
            continue

        incat = True
        if ans == "-":
            gold_v = None
            incat = False
        elif ans == "":
            gold_v = p.get("venue")
        else:
            gold_v = ans

        city_in = input(f"  city  [{p.get('city') or ''}] (- = none): ").strip()
        gold_c = None if city_in == "-" else (p.get("city") if city_in == "" else city_in)

        print("   " + "  ".join(f"{k+1}:{c}" for k, c in enumerate(CATS)))
        cat_in = input(f"  cat   [{p.get('category') or 'other'}]: ").strip()
        gold_cat = CATS[int(cat_in) - 1] if cat_in.isdigit() and 1 <= int(cat_in) <= 8 \
            else (p.get("category") or "other")

        r["gold"] = {"venue": gold_v, "city": gold_c, "category": gold_cat, "in_catalog": incat}
        r["verdict"] = {
            "venue": "right" if (gold_v is None and not p.get("venue"))
            or _norm(p.get("venue")) == _norm(gold_v) else ("missing" if not p.get("venue") else "wrong"),
            "city": "right" if _norm(p.get("city")) == _norm(gold_c) else ("missing" if not p.get("city") else "wrong"),
            "category": "right" if p.get("category") == gold_cat else "wrong",
        }
        r["note"] = (input("  note (optional): ").strip() or r.get("note", ""))
        rows[idx] = r
        _save(rows)               # persist after every row
        print("  ✓ saved")

    print(f"\ndone — {CORPUS}", file=sys.stderr)


if __name__ == "__main__":
    main()
