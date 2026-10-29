"""What information is MISSING from the inputs, by split (counts + shortcodes only):
cut-off captions, reels flagged for recapture, missing display names, reposts that cross splits,
and suspicious answer-key labels."""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from burner_common import corpus, ev, gold_new, silver_new  # noqa: E402

by = defaultdict(lambda: defaultdict(list))
for r in corpus:
    s, code, inp, g = ev.split_of(r), ev._code(r["url"]), r.get("input") or {}, r.get("gold") or {}
    if r.get("input_fidelity") == "first_line_only" or r.get("needs_recapture"):
        by["caption cut off / needs recapture"][s].append(code)
    if not inp.get("author_name"):
        by["display name missing"][s].append(code)
    gv = g.get("venue") or ""
    if gv and " " not in gv and len(gv) >= 11 and gv[1:].islower():
        by["LABEL: gold venue looks like a run-together handle"][s].append(f"{code}={gv}")
    if gv and g.get("city") and gv.lower().endswith(" " + g["city"].lower()):
        by["LABEL: gold venue ends with its city"][s].append(f"{code}={gv}")
    if (r.get("verdict") or {}).get("venue") == "needs_review":
        by["LABEL: still needs_review (not scored)"][s].append(code)

caps = defaultdict(set)
for r in corpus + gold_new + silver_new:
    cap = " ".join(((r.get("input") or {}).get("caption") or "").split())[:200].lower()
    if len(cap) > 40:
        caps[cap].add((ev._code(r["url"]), ev.split_of(r), ev.creator_of(r)))
for cap, items in caps.items():
    splits = {s for _, s, _ in items}
    if len(items) > 1:
        key = "repost CROSSES splits (leak risk)" if len(splits) > 1 else "repost within one split (duplicate)"
        by[key]["/".join(sorted(splits))].append(",".join(sorted(c for c, _, _ in items)))

for problem, splits in by.items():
    total = sum(len(v) for v in splits.values())
    print(f"\n{problem}: {total}")
    for s, items in sorted(splits.items()):
        show = items if problem.startswith(("LABEL: gold", "repost")) else items[:5]
        print(f"   {s:10} {len(items):4}  {', '.join(show)}{' …' if len(items) > len(show) else ''}")
