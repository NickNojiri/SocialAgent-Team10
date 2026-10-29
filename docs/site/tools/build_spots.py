"""Build the real spots behind docs/site/spotbot.html from SpotBot's hand-labelled reels.

Reads fixtures/labels.jsonl (the gold venue, city and category of every labelled reel) and writes
docs/site/la/spots.js, the places on the 3D map that the page's cards, reels and pins use.

Only facts about the places go on the page: name, neighbourhood, category, where it is, how many
labelled reels shared it and the dish. No captions, handles or links from anyone's post.

Where a place is, in order of trust:
  1. the reel's own location tag, when the tag names the venue itself ("Republique"). A tag that
     only names a city or district ("Los Angeles, California", "Silver Lake") is skipped: every reel
     tagged that way lands on the same spot, which is what stacked the pins up.
  2. data/places/index.parquet (Overture Maps places; built by experiments/burner_study/places_lookup.py),
     by exact name, near the city the label gives, and only when the name has one location there
     (a chain with five LA branches can't say which one the reel was at).
Anything off the 3D map, or not certain, is left out.

Usage: python docs/site/tools/build_spots.py
"""
import collections
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
LABELS = ROOT / "fixtures" / "labels.jsonl"
PLACES = ROOT / "data" / "places" / "index.parquet"
OUT = ROOT / "docs" / "site" / "la" / "spots.js"
SOUTH, WEST, NORTH, EAST = 33.96, -118.54, 34.30, -118.10      # the 3D map (build_la3d.py)
NEAR_CITY = 6000                                                # metres from the labelled city
ONE_PLACE = 300                                                 # hits closer than this are one place

# LA neighbourhoods, roughly at their centres: what "Los Angeles" is called on a card, and where a
# label that names a neighbourhood instead of a city is
HOODS = {
    "Downtown LA": (34.0480, -118.2510), "Little Tokyo": (34.0500, -118.2400), "Arts District": (34.0410, -118.2330),
    "Chinatown": (34.0623, -118.2383), "Koreatown": (34.0618, -118.3004), "Hollywood": (34.1000, -118.3300),
    "Silver Lake": (34.0870, -118.2700), "Echo Park": (34.0780, -118.2610), "Los Feliz": (34.1070, -118.2840),
    "Fairfax": (34.0760, -118.3610), "Mid-Wilshire": (34.0625, -118.3450), "Mid-City": (34.0450, -118.3500),
    "West Adams": (34.0330, -118.3300), "Pico-Robertson": (34.0550, -118.3850), "Westwood": (34.0600, -118.4450),
    "Sawtelle": (34.0380, -118.4430), "Palms": (34.0220, -118.4040), "Mar Vista": (34.0040, -118.4300),
    "Venice": (33.9900, -118.4650), "Boyle Heights": (34.0330, -118.2050), "Highland Park": (34.1110, -118.1920),
    "Century City": (34.0570, -118.4170), "Atwater Village": (34.1170, -118.2600), "Larchmont": (34.0750, -118.3240),
}
ALIASES = {"downtown los angeles": "Downtown LA", "dtla": "Downtown LA"}


def squash(s) -> str:
    """Lowercase letters/digits only, accents dropped, '&' read as 'and' (as places_lookup.py does)."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s.replace("&", " and ")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def metres(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 110574, (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])))


def on_map(lat, lng) -> bool:
    return SOUTH < lat < NORTH and WEST < lng < EAST


def shared_run(a: str, b: str) -> int:
    """Length of the longest run of letters two names share."""
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def main() -> None:
    rows = [json.loads(line) for line in LABELS.read_text(encoding="utf-8").split("\n") if line.strip()]
    venues = collections.defaultdict(list)
    tagged = collections.defaultdict(set)                       # coordinate -> the venues tagged there
    for r in rows:
        g, i = r["gold"], r["input"]
        if not g.get("venue") or not g.get("in_catalog", True) or g.get("venues"):
            continue
        venues[g["venue"]].append(r)
        if i.get("lat") is not None:
            tagged[(round(i["lat"], 3), round(i["lng"], 3))].add(squash(g["venue"]))

    t = pq.read_table(PLACES, columns=["name", "city", "lat", "lng", "confidence"])
    keep = pc.and_(pc.and_(pc.greater(t["lat"], SOUTH), pc.less(t["lat"], NORTH)), pc.and_(pc.greater(t["lng"], WEST), pc.less(t["lng"], EAST)))
    index, cities = collections.defaultdict(list), collections.defaultdict(list)
    for p in t.filter(keep).to_pylist():
        index[squash(p["name"])].append(p)
        if p["city"]:
            cities[p["city"].lower()].append((p["lat"], p["lng"]))
    centre = {c: (sorted(a for a, _ in xs)[len(xs) // 2], sorted(b for _, b in xs)[len(xs) // 2]) for c, xs in cities.items() if len(xs) >= 50}
    for name, ll in HOODS.items():
        centre[name.lower()] = ll
    for alias, name in ALIASES.items():
        centre[alias] = HOODS[name]

    spots = []
    for venue, rs in venues.items():
        key = squash(venue)
        if len(key) < 3:
            continue
        gold = rs[0]["gold"]
        city = (gold.get("city") or "").strip()
        ll, how = None, None
        for r in rs:                                            # 1. the reel's own tag, if it names the venue
            i = r["input"]
            if i.get("lat") is None or not on_map(i["lat"], i["lng"]):
                continue
            if len(tagged[(round(i["lat"], 3), round(i["lng"], 3))]) > 1:
                continue
            if shared_run(key, squash(i.get("location_text"))) >= min(5, len(key)):
                ll, how = (i["lat"], i["lng"]), "tag"
                break
        if ll is None and city.lower() in centre:              # 2. one place by that name near the labelled city
            hits = [p for p in index.get(key, []) if metres((p["lat"], p["lng"]), centre[city.lower()]) < NEAR_CITY]
            if hits and all(metres((p["lat"], p["lng"]), (hits[0]["lat"], hits[0]["lng"])) < ONE_PLACE for p in hits):
                best = max(hits, key=lambda p: p["confidence"] or 0)
                ll, how = (best["lat"], best["lng"]), "places"
        if ll is None:
            continue
        hood = min(HOODS, key=lambda h: metres(ll, HOODS[h]))
        la = city.lower() in ("los angeles", "la", "") or city in HOODS or city.lower() in ALIASES
        area = hood if la and metres(ll, HOODS[hood]) < 1800 else (city if not la else "Los Angeles")
        dish = next((r["gold"].get("dish") or r["gold"].get("cuisine") for r in rs if r["gold"].get("dish") or r["gold"].get("cuisine")), None)
        spots.append({"venue": venue, "area": area, "cat": gold.get("category") or "food_drink",
                      "ll": [round(ll[0], 5), round(ll[1], 5)], "reels": len(rs), "dish": dish, "from": how})

    spots.sort(key=lambda s: (-s["reels"], s["venue"]))
    body = ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in spots)
    OUT.write_text(
        "// The real spots on the 3D map, from SpotBot's hand-labelled reels (fixtures/labels.jsonl).\n"
        "// Written by docs/site/tools/build_spots.py; don't edit by hand. Places: Overture Maps (CDLA-Permissive-2.0).\n"
        f"window.SPOTS = [\n{body}\n];\n", encoding="utf-8", newline="\n")
    by = collections.Counter(s["from"] for s in spots)
    print(f"{len(spots)} spots on the map ({by['tag']} from their own tag, {by['places']} from the places index) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
