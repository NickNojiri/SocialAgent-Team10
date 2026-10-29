"""Does a local places lookup (Overture, SoCal + NYC) improve venue names? Experiment only.

Starts from the parser + the rules kept in rules_ladder.py (1c, 2a, 2d), then tries:
  3a  snap: replace the predicted name with a known place whose name is its start, in a city
      the reel mentions ('Tacolandofficial' -> 'Taco Land')
  3b  fill: when there is no venue, take a known place name (from a mentioned city) found
      word-for-word in the caption or location tag
Same decision rule as rules_ladder: gold train + silver decide, gold train may not get worse;
dev only confirms. Also reports the tolerant score.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
src = (HERE / "rules_ladder.py").read_text(encoding="utf-8")
exec(src.split("print(f\"{'start: parser'")[0])          # sets, STEPS, same(), tolerant(), N, ev, flat
from places_lookup import places, squash  # noqa: E402

P = places()
KEPT = {"1c", "2a", "2d"}


def cities_of(x):
    """Cities the reel itself mentions: the parser's geo names, the location tag, and any
    known city name written in the caption. Never the gold label."""
    cand = N.normalize(x["raw"])
    geo = cand.get("geo")
    names = list(getattr(geo, "place_names", None) or [])
    if getattr(geo, "raw_location_text", None):
        names += [p.strip() for p in geo.raw_location_text.split(",")]
    if x["raw"].location_text:
        names += [p.strip() for p in x["raw"].location_text.split(",")]
    words = re.findall(r"[A-Za-z]+", x["cap"])
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            c = squash(" ".join(words[i:i + n]))
            if len(c) >= 4 and c in P.by_city:
                names.append(" ".join(words[i:i + n]))
    return {squash(c) for c in names if c} & set(P.by_city)


for k, xs in sets.items():
    for x in xs:
        x["cities"] = cities_of(x)
cur = {k: [x["venue"] for x in xs] for k, xs in sets.items()}
for name, fn in STEPS:
    if name[:2] in KEPT:
        cur = {k: [fn(x, v) for x, v in zip(sets[k], cur[k])] for k in sets}
base = {k: list(v) for k, v in cur.items()}


def snap(x, v):
    if not v or not x["cities"] or P.exact(v, x["cities"]):
        return v
    return P.prefix(v, x["cities"]) or v


def fill(x, v):
    if v or not x["cities"] or x["slot"] == "list":       # a list names several places: no single venue
        return v
    found = P.names_in_text(x["cap"] + "\n" + (x["raw"].location_text or ""), x["cities"], min_len=7)
    found = [f for f in found if not N._is_region(f) and not N._is_container(f)]
    return found[0] if found else v


covered = {k: sum(bool(x["cities"]) for x in xs) for k, xs in sets.items()}
print("reels with a city the lookup covers:", {k: f"{covered[k]}/{len(sets[k])}" for k in sets})
print(f"{'start: parser + kept rules':40} train {sum(same(x, v) for x, v in zip(sets['train'], cur['train']))}"
      f" | dev {sum(same(x, v) for x, v in zip(sets['dev'], cur['dev']))}/{len(sets['dev'])}")
for name, fn in (("3a snap to a known place (prefix)", snap), ("3b fill from known names in text", fill)):
    new = {k: [fn(x, v) for x, v in zip(sets[k], cur[k])] for k in sets}
    tr, f, b = show(name, cur["train"], new["train"], sets["train"])
    dv, _, _ = show(name, cur["dev"], new["dev"], sets["dev"])
    sv, sf, sb = show(name, cur["silver"], new["silver"], sets["silver"])
    keep = (f + sf > b + sb) and f >= b
    print(f"{name:40} {'KEEP' if keep else 'drop'}\n    train {tr}\n    dev   {dv}\n    silver {sv}")
    for split in (["train", "silver"] if "--train-changes" in sys.argv else ["dev"]):
        changed = [(x["code"], v, w, x["gold"], same(x, v), same(x, w))
                   for x, v, w in zip(sets[split], cur[split], new[split]) if v != w]
        for code, v, w, g, a, b in changed:
            mark = "FIX " if b and not a else "BREAK" if a and not b else "same"
            print(f"      {split} {mark} {code}: {v!r} -> {w!r}  (gold {g!r})")
    if keep:
        cur = new
print("\nvs parser + kept rules on dev:", show("", base["dev"], cur["dev"], sets["dev"])[0])
for k in ("train", "dev"):
    xs = sets[k]
    print(f"tolerant ({k}): {sum(tolerant(x, v) for x, v in zip(xs, base[k]))} -> {sum(tolerant(x, v) for x, v in zip(xs, cur[k]))} of {len(xs)}")

# 3c: category from the lookup. When the (final) venue is a known place in a city the reel
# mentions, and the parser's category isn't one the place has, use the place's category.
by_code = {ev._code(r["url"]): r for r in corpus + gold_new + silver_new}
PRIORITY = ["nightlife", "live_music", "cafe_dessert", "market_popup", "outdoors", "community", "food_drink", "other"]


def cat_fix(x, v, parser_cat):
    if not v or not x["cities"]:
        return parser_cat
    cats = P.categories(v, x["cities"])
    if not cats or parser_cat in cats:
        return parser_cat
    return next(c for c in PRIORITY if c in cats)


print("\n3c category from the lookup (scored on reels with a gold category):")
fx = br = 0
for k in ("train", "silver", "dev"):
    before = after = n = 0
    f = b = 0
    for x, v in zip(sets[k], cur[k]):
        row = by_code[x["code"]]
        gold_cat = (row.get("gold") or {}).get("category")
        if not gold_cat or (row.get("verdict") or {}).get("category") not in ("right", "wrong"):
            continue
        pc = getattr(N.normalize(x["raw"]).get("category"), "value", None)
        nc = cat_fix(x, v, pc)
        n += 1
        before += pc == gold_cat
        after += nc == gold_cat
        f += nc == gold_cat and pc != gold_cat
        b += pc == gold_cat and nc != gold_cat
    if k != "dev":
        fx, br = fx + f, br + b
    print(f"    {k:6} {before}/{n} -> {after}/{n}  fixed {f} broke {b}")
print(f"    decision (train + silver): {'KEEP' if fx > br else 'drop'}")
