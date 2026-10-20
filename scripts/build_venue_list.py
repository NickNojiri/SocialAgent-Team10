"""Build fixtures/venues_socal.json: names of real food, drink and nightlife places in
Los Angeles and Orange County, from OpenStreetMap.

    python scripts/build_venue_list.py            # one Overpass query, ~1 minute
    python scripts/build_venue_list.py --dry-run  # print the query, fetch nothing

The candidate ranker (experiments/ranker.py) asks "is this candidate a real place?"
with it. One query per run, well inside the Overpass usage policy; don't loop it.

The names are © OpenStreetMap contributors and the file is under the Open Database
License (ODbL 1.0): https://www.openstreetmap.org/copyright. Keep that notice with it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "fixtures" / "venues_socal.json"
ENDPOINT = "https://overpass-api.de/api/interpreter"
BBOX = (33.40, -118.95, 34.35, -117.40)          # south, west, north, east: LA + Orange County
AMENITY = "restaurant|cafe|bar|pub|fast_food|ice_cream|food_court|nightclub|biergarten|marketplace"
SHOP = "bakery|deli|coffee|tea|confectionery|pastry|ice_cream|beverages|chocolate|cheese|wine"
NOTICE = ("Names (c) OpenStreetMap contributors, Open Database License (ODbL 1.0), "
          "https://www.openstreetmap.org/copyright")


def query() -> str:
    box = ",".join(str(v) for v in BBOX)
    return (f'[out:json][timeout:180];(nwr["name"]["amenity"~"^({AMENITY})$"]({box});'
            f'nwr["name"]["shop"~"^({SHOP})$"]({box}););out tags;')


def names(overpass_json: dict) -> list[str]:
    """Every name, brand and alternative name in the result, de-duplicated, sorted."""
    found: dict[str, str] = {}
    for el in overpass_json.get("elements", []):
        tags = el.get("tags") or {}
        for key in ("name", "brand", "alt_name", "name:en", "short_name"):
            for value in (tags.get(key) or "").split(";"):
                value = " ".join(value.split())
                if 3 <= len(value) <= 80:
                    found.setdefault(value.casefold(), value)
    return sorted(found.values(), key=str.casefold)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the query and stop")
    args = ap.parse_args(argv)
    q = query()
    if args.dry_run:
        print(q)
        return 0
    req = urllib.request.Request(
        ENDPOINT, data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "SpotBot-capstone/1.0 (CSULB CECS 491A; venue name list, one query)"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    venues = names(data)
    OUT.write_text(json.dumps({"notice": NOTICE, "bbox": BBOX, "names": venues}, ensure_ascii=False, indent=0)
                   + "\n", encoding="utf-8", newline="\n")
    print(f"{len(venues)} venue names from {len(data.get('elements', []))} OpenStreetMap places -> "
          f"{OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
