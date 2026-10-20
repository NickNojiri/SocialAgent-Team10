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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "fixtures" / "venues_socal.json"
# The main Overpass server first, then two public mirrors. A busy server answers
# 429/502/503/504; each gets one try, with a pause between, so a run sends at most 3 queries.
ENDPOINTS = ("https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter")
BUSY = {429, 502, 503, 504}
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


def fetch(q: str, endpoints: tuple[str, ...] = ENDPOINTS, pause: float = 30.0, open_url=None) -> dict:
    """The query's JSON from the first server that isn't busy."""
    open_url = open_url or urllib.request.urlopen
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for i, url in enumerate(endpoints):
        if i:
            time.sleep(pause)
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": "SpotBot-capstone/1.0 (CSULB CECS 491A; venue name list, one query)"})
        try:
            with open_url(req, timeout=240) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code not in BUSY:
                raise
            last = err
            print(f"{url}: busy ({err.code}), trying the next server", file=sys.stderr)
    raise RuntimeError(f"every Overpass server was busy (last: {last}); try again later")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the query and stop")
    args = ap.parse_args(argv)
    q = query()
    if args.dry_run:
        print(q)
        return 0
    data = fetch(q)
    venues = names(data)
    OUT.write_text(json.dumps({"notice": NOTICE, "bbox": BBOX, "names": venues}, ensure_ascii=False, indent=0)
                   + "\n", encoding="utf-8", newline="\n")
    print(f"{len(venues)} venue names from {len(data.get('elements', []))} OpenStreetMap places -> "
          f"{OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
