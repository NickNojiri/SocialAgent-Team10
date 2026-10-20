"""Why does venue extraction fail? Sort every miss into one cause bucket.

    python experiments/error_buckets.py                # train split (tune here)
    python experiments/error_buckets.py --show 5       # + 5 examples per bucket
    python experiments/error_buckets.py --split dev    # confirm a rule on creators it never saw
    python experiments/error_buckets.py --split test --final "why"   # read once, to report

Tune on train, check on dev (fixtures/splits.json keeps each creator in one split), and
leave test alone (docs/DATA_PLAN.md).

Buckets (first rule that matches wins, in this order)
  not_in_input     gold venue appears nowhere in the stored text: capture can't see it
                   (on screen, comments, outside knowledge). No extractor rule fixes it.
  should_be_empty  gold has no venue (promo, recipe, list, no_info) but a venue came out
  missed           gold venue is in the text, the extractor returned nothing
  poster_name      returned the poster's own handle / display name, but it's a reviewer
  place_name       returned a city, region, country or complex ("Disneyland")
  form_only        right place, wrong form: glued handle, extra word, cut short
                   ("Castawayburbank", "Pullman Bread Las") — a cleanup problem
  wrong_mention    returned another @handle or name from the caption
  other            anything else
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import load_labels, raw_from_input, read_test, split_of, use_aliases, venue_same_place  # noqa: E402
from src.ingestion.pipeline.normalizer import _AREA_CITY_SET, _CONTAINERS, _REGION_STOP, normalize  # noqa: E402

_PLACES = {re.sub(r"[^a-z0-9]", "", p.lower()) for p in (_AREA_CITY_SET | _REGION_STOP | _CONTAINERS)}
_FIELDS = ("caption", "og_title", "handle", "ocr", "transcript", "hashtags", "location_text", "author_name")


def sq(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _lcs(a: str, b: str) -> int:
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def bucket(row: dict, pred: str | None) -> str | None:
    """None when the prediction is right."""
    gold = (row.get("gold") or {}).get("venue")
    if venue_same_place(pred, gold):
        return None
    inp = row.get("input") or {}
    text = sq(" ".join(str(inp.get(f) or "") for f in _FIELDS))
    if not gold:
        return "should_be_empty"
    if sq(gold) not in text:
        return "not_in_input"
    if not pred:
        return "missed"
    p, g = sq(pred), sq(gold)
    poster = {sq(inp.get("handle")), sq(inp.get("author_name"))} - {""}
    if p in poster or any(p and (p in x or x in p) for x in poster) and _lcs(p, g) < 4:
        return "poster_name"
    if p in _PLACES:
        return "place_name"
    if _lcs(p, g) >= max(4, min(len(p), len(g)) * 0.6):
        return "form_only"
    if p in text:
        return "wrong_mention"
    return "other"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["train", "dev", "test", "all"], default="train")
    ap.add_argument("--final", metavar="WHY", help="required with --split test (logged in docs/results/TEST_LEDGER.md)")
    ap.add_argument("--show", type=int, default=0, help="examples to print per bucket")
    args = ap.parse_args()
    if args.split == "test":
        read_test(args.final, "error_buckets")

    use_aliases("train" if args.split == "test" else "fit")
    rows = [r for r in load_labels() if r.get("gold") and r.get("verdict", {}).get("venue") != "needs_review"
            and (args.split == "all" or split_of(r) == args.split)
]
    counts, examples = Counter(), defaultdict(list)
    for r in rows:
        c = normalize(raw_from_input(r["url"], r.get("input") or {}))
        b = bucket(r, c.get("venue_name"))
        if b:
            counts[b] += 1
            examples[b].append((c.get("venue_name"), c.get("venue_slot"), r["gold"].get("venue"),
                                r.get("post_type", ""), r["url"]))
    wrong = sum(counts.values())
    print(f"{args.split}: {len(rows)} reels, {len(rows) - wrong} right, {wrong} wrong\n")
    print(f"  {'bucket':<16}{'n':>4}  {'share':>6}   fixable by")
    fix = {"not_in_input": "capture (OCR, comments, tags)", "should_be_empty": "place-or-not gate",
           "missed": "a new rule / the model", "poster_name": "blogger detection",
           "place_name": "place stop-list", "form_only": "name cleanup",
           "wrong_mention": "ranking between candidates", "other": "look by hand"}
    for b, n in counts.most_common():
        print(f"  {b:<16}{n:>4}  {100 * n / wrong:5.1f}%   {fix[b]}")
        for pred, slot, gold, ptype, url in examples[b][:args.show]:
            print(f"      pred={pred!r} [{slot}]  gold={gold!r} {ptype}  {url}")


if __name__ == "__main__":
    main()
