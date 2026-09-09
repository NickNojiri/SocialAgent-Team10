"""Label the reels the bot couldn't figure out.

    python scripts/review.py            # label the unlabeled rows
    python scripts/review.py --misses   # re-check already-labeled rows the bot still gets wrong
    python scripts/review.py --no-open  # don't auto-open the browser

For each reel it opens the video in your browser, shows the caption and the
bot's guess, and asks 3 things. Answers save immediately — press q to stop and
just run it again to pick up where you left off.
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
    ap.add_argument("--misses", action="store_true",
                    help="re-audit extractor-wrong rows that are ALREADY labeled")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    try:
        from src.ingestion.eval import raw_from_input
        from src.ingestion.pipeline.normalizer import normalize
    except Exception:  # noqa: BLE001
        raw_from_input = normalize = None

    rows = _load()
    todo = []
    for idx, r in enumerate(rows):
        v = r.get("verdict", {})
        labeled = (r.get("gold") or {}).get("venue") or (r.get("gold") or {}).get("in_catalog") is False
        if v.get("venue") == "needs_review":
            todo.append(idx)
        elif args.misses and v.get("venue") in ("wrong", "missing") and labeled:
            todo.append(idx)

    print(f"""
{len(todo)} reels to label.  For each one:

  Q1 venue  — the ONE place the reel is about (a restaurant / bar / cafe / bakery).
              Type its name.  Press ENTER to accept the [bracketed] guess.
              Type  x  if it's NOT about one place (a recipe, an ad, a meme).
              Type  ?  if you can't tell — comes back later.
  Q2 city   — the city or neighborhood.  ENTER accepts the guess.  Type  x  for none.
  Q3 type   — pick a number 1-8.  ENTER keeps the guess.

  Type  q  at any question to stop (your work is saved; just re-run to continue).
""", file=sys.stderr)

    CAT_MENU = "  1 food/drink   2 cafe/dessert   3 nightlife/bar   4 live music\n" \
               "  5 market/popup  6 outdoors       7 community       8 other"

    for n, idx in enumerate(todo, 1):
        r = rows[idx]
        code = r["url"].rstrip("/").split("/")[-1]
        i = r.get("input", {})
        g = r.get("gold", {})
        cap = " ".join((i.get("caption") or "").split())

        guess_v = r.get("predicted", {}).get("venue")
        guess_c = r.get("predicted", {}).get("city")
        guess_cat = r.get("predicted", {}).get("category") or "other"
        if normalize is not None:                 # re-run the parser for a live guess
            try:
                c = normalize(raw_from_input(r["url"], i))
                guess_v = c.get("venue_name")
                gp = getattr(c.get("geo"), "place_names", None) or []
                guess_c = gp[-1] if gp else None
                guess_cat = getattr(c.get("category"), "value", guess_cat)
            except Exception:  # noqa: BLE001
                pass

        if not args.no_open:
            embed = f"https://www.instagram.com/p/{code}/embed/captioned/"  # no login wall
            subprocess.Popen(["xdg-open", embed],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print("\n" + "─" * 72)
        print(f"  REEL {n} of {len(todo)}   ·   {r['url']}   ·   @{i.get('handle')}")
        print(f"  caption   “{cap[:400]}”")
        if i.get("transcript"):
            print(f"  spoken    “{' '.join(i['transcript'].split())[:220]}”")
        if i.get("venue_candidate"):
            print(f"  IG tag    {i['venue_candidate']}")
        if g.get("venue") or g.get("in_catalog") is False:
            print(f"  (already labeled: {g.get('venue')!r} / {g.get('city')!r} / {g.get('category')})")
        print(f"  BOT GUESS   venue: {guess_v or '—'}   city: {guess_c or '—'}   type: {guess_cat}")
        print()

        try:
            ans = input(f"  Q1 venue?  [{guess_v or ''}]  (name / ENTER=guess / x=not a place / ?=later / q=quit)\n     > ").strip()
        except EOFError:
            break
        if ans.lower() == "q":
            break
        if ans == "?":
            continue

        incat = True
        if ans.lower() == "x":
            gold_v, incat = None, False
        elif ans == "":
            gold_v = guess_v
        else:
            gold_v = ans

        city_in = input(f"  Q2 city?   [{guess_c or ''}]  (name / ENTER=guess / x=none / q=quit)\n     > ").strip()
        if city_in.lower() == "q":
            break
        gold_c = None if city_in.lower() == "x" else (guess_c if city_in == "" else city_in)

        print(CAT_MENU)
        cat_in = input(f"  Q3 type?   [{guess_cat}]  (1-8 / ENTER=guess / q=quit)\n     > ").strip()
        if cat_in.lower() == "q":
            break
        gold_cat = CATS[int(cat_in) - 1] if cat_in.isdigit() and 1 <= int(cat_in) <= 8 else guess_cat

        r["gold"] = {"venue": gold_v, "city": gold_c, "category": gold_cat, "in_catalog": incat}
        r["verdict"] = {
            "venue": "right" if (gold_v is None and not guess_v) or _norm(guess_v) == _norm(gold_v)
            else ("missing" if not guess_v else "wrong"),
            "city": "right" if _norm(guess_c) == _norm(gold_c) else ("missing" if not guess_c else "wrong"),
            "category": "right" if guess_cat == gold_cat else "wrong",
        }
        r["note"] = r.get("note", "")
        rows[idx] = r
        _save(rows)
        print(f"  ✓ saved  →  {gold_v or ('NOT A PLACE' if not incat else '(none)')}")

    left = sum(1 for x in rows if x.get("verdict", {}).get("venue") == "needs_review")
    print(f"\nstopped — {left} still unlabeled. re-run to continue.", file=sys.stderr)


if __name__ == "__main__":
    main()
