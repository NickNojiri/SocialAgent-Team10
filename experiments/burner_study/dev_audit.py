"""#4 flag suspicious dev gold venues; #5 bucket the remaining dev misses (parser + kept rules).
Prints shortcodes, venue names and bucket counts only (no captions)."""
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
DATA = REPO / "data"
src = (HERE / "rules_ladder.py").read_text(encoding="utf-8")
exec(src.split("print(f\"{'start: parser'")[0])          # sets, STEPS, same(), helpers
KEPT = {"1c", "2a", "2d"}
dev = sets["dev"]
venues = [x["venue"] for x in dev]
for name, fn in STEPS:
    if name[:2] in KEPT:
        venues = [fn(x, v) for x, v in zip(dev, venues)]

print("── #4 suspicious dev gold venues ──")
for x in dev:
    g = x["gold"] or ""
    handles = {flat(m) for m in re.findall(r"@([\w.]+)", x["cap"])}
    why = []
    if g and " " not in g and len(g) >= 12 and g[:1].isupper() and g[1:].islower():
        why.append("one long run-together word (smashed handle?)")
    if g and flat(g) in handles and " " not in g and len(g) > 8:
        why.append("equals an @handle with no spaces")
    if g and (re.search(r"[#@📍]|https?://|\d{5}", g) or len(g) > 45):
        why.append("has #/@/📍/link/zip or is very long")
    if why:
        print(f"  {x['code']}: {g!r}  <- {'; '.join(why)}")

print("\n── #5 remaining dev misses ──")
buckets = Counter()
for x, v in zip(dev, venues):
    if same(x, v):
        continue
    g = x["gold"]
    visible = bool(g) and (bool(re.search(re.escape(g), x["cap"], re.I))
                           or flat(g) in {flat(m) for m in re.findall(r"@([\w.]+)", x["cap"])}
                           or flat(g)[:8] in flat(x["raw"].location_text))
    if not g:
        b = "not a place, parser invented a venue"
    elif not visible:
        b = "venue NOT in caption/@/tag (needs audio/visual/knowledge)"
    elif not v:
        b = "venue visible, parser found nothing"
    else:
        b = "venue visible, parser picked other text"
    buckets[b] += 1
    print(f"  [{b[:28]}] {x['code']}: pred {v!r} gold {g!r}")
print()
for b, n in buckets.most_common():
    print(f"{n:3}  {b}")
