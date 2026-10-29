"""Where every dev point goes: each of the 106 scoreable dev reels lands in exactly one bucket.

A  right today (parser, 'same place')
A2 right place, name a bit off (counts under Nick's tolerance, not under the strict scorer)
B  wrong, but the right name IS on the candidate list  (a picking problem)
     B1 the right answer is the poster itself (venue's own account)
     B2 the right answer is in the location tag
     B3 the right answer is other caption text (@mention, 📍, title case, hashtag, lookup)
C  not a place, but the parser named a venue
D  the right name is in no text we read (caption / @ / tag / lookup)
     D1 ...and the video's speech or screen text has it (dev reels transcribed so far)
     D2 ...not found anywhere yet
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ranker_v3 as V  # noqa: E402
from burner_common import corpus, ev, squash  # noqa: E402

av = {}
p = V.HERE.parents[1] / "data" / "experiments" / "dev_av.jsonl"
if p.exists():
    for line in p.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            d = json.loads(line)
            av[d["code"]] = d

def tolerant(pred, gold):
    if ev.venue_same_place(pred, gold):
        return True
    a, b = squash((pred or "").replace("&", "and")), squash((gold or "").replace("&", "and"))
    return bool(a and b) and min(len(a), len(b)) >= 4 and (a in b or b in a)


buckets, codes = Counter(), defaultdict(list)
dev = [r for r in corpus if ev.split_of(r) == "dev" and V.scoreable(r)]
transcribed = 0
for row in dev:
    r = V.Reel(row, 0.5)
    code = ev._code(row["url"])
    pred, gold = r.rules_pred, r.gold
    if ev.venue_same_place(pred, gold):
        b = "A  right today"
    elif gold and pred and tolerant(pred, gold):
        b = "A2 right place, name a bit off"
    elif not gold:
        b = "C  not a place, parser named a venue"
    elif r.target >= 0:
        src = r.cands[r.target].sources
        b = ("B1 answer = the poster (own account)" if "author" in src else
             "B2 answer = the location tag" if "location_tag" in src else
             "B3 answer = other caption text")
    else:
        x = av.get(code)
        heard = x and squash(gold) and squash(gold)[:8] in squash((x.get("transcript") or "") + " " + (x.get("ocr") or ""))
        b = "D1 only in the video (speech/screen)" if heard else "D2 not found in any text yet"
    transcribed += code in av
    buckets[b] += 1
    codes[b].append(code)

n = len(dev)
print(f"dev reels: {n} (transcribed so far: {transcribed})")
run = 0
for b in sorted(buckets):
    run += buckets[b]
    print(f"  {b:42} {buckets[b]:3}  {100 * buckets[b] / n:5.1f} pts")
if "--codes" in sys.argv:
    for b in sorted(codes):
        print(b, codes[b])
