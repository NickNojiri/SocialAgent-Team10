"""Does "near <place>" return what's actually near? Expected vs SpotBot, side by side.

    python experiments/location_eval.py                    # real stack: Ollama embeddings + LLM re-rank
    python experiments/location_eval.py --stand-in         # offline: keyword embedder, no re-rank
    python experiments/location_eval.py --query "tacos near Laguna Niguel" --match "tacos"

Builds a throwaway catalog from the label corpus (every reel, run through the real
extraction from its stored input, so the catalog holds what capture would have saved),
sends each request through the real RecommendationService, and prints:

  expected  the answer key: the 3 nearest reels whose *labels* match the request
  SpotBot   exactly what the bot would post (discord_format), plus each spot's true distance

A reel's true location is its Instagram location tag, unless that tag is only a county
or state; otherwise the centre of its labelled city (_CITY, approximate). Reels with
neither are left out of the answer key, because we can't judge them.

--stand-in swaps the embedding model for a bag-of-words hash, so retrieval becomes
keyword matching instead of meaning. Everything after retrieval (relevance floor, top-3
cut, distance sort, formatting) is the production code either way, which is the part
this script judges.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.eval import load_labels, raw_from_input, use_aliases, venue_same_place  # noqa: E402
from src.ingestion.pipeline.validator import build_record  # noqa: E402
from src.ingestion.serving.discord_format import format_recommendations  # noqa: E402
from src.ingestion.serving.recommender import RecommendationService, _haversine_km, extract_origin_text  # noqa: E402
from src.ingestion.sinks.chroma_sink import ChromaSink  # noqa: E402

# City centres, approximate (±1 km): enough to judge "near" across Orange County.
_CITY = {
    "laguna niguel": (33.5225, -117.7076), "laguna beach": (33.5427, -117.7854),
    "laguna hills": (33.5963, -117.6981), "aliso viejo": (33.5676, -117.7256),
    "dana point": (33.4672, -117.6981), "mission viejo": (33.6000, -117.6720),
    "san juan capistrano": (33.5017, -117.6625), "san clemente": (33.4270, -117.6120),
    "lake forest": (33.6469, -117.6892), "rancho santa margarita": (33.6409, -117.6031),
    "irvine": (33.6846, -117.8265), "newport beach": (33.6189, -117.9298),
    "costa mesa": (33.6411, -117.9187), "tustin": (33.7458, -117.8262),
    "santa ana": (33.7455, -117.8677), "orange": (33.7879, -117.8531),
    "huntington beach": (33.6595, -117.9988), "fountain valley": (33.7092, -117.9537),
    "garden grove": (33.7743, -117.9380), "westminster": (33.7513, -117.9940),
    "anaheim": (33.8366, -117.9143), "fullerton": (33.8704, -117.9242),
}
_COARSE_TAG = re.compile(r"(?i)^\s*(\w+ county|california|southern california|socal)\b")
_STOP = {"near", "around", "in", "the", "a", "and", "of", "at", "im", "for", "whats", "good",
         "venue", "category", "vibe"}


def _dish_or_name(word: str):
    return lambda g: word in (g.get("dish") or []) or word in (g.get("venue") or "").lower()


# request template → which labels count as a right answer
DEFAULT_QUERIES = {
    "sushi near {near}": _dish_or_name("sushi"),
    "dessert near {near}": lambda g: g.get("category") == "cafe_dessert",
    "coffee near {near}": lambda g: "coffee" in (g.get("dish") or [])
    or bool(re.search(r"coffee|caf[eé]", (g.get("venue") or "").lower())),
    "im in {near} whats good for dinner": lambda g: g.get("category") == "food_drink",
}


def located(row: dict):
    """(lat, lng, how) for a reel's true location, or None when we can't tell."""
    inp, gold = row.get("input") or {}, row.get("gold") or {}
    if inp.get("lat") is not None and inp.get("lng") is not None \
            and not _COARSE_TAG.match(inp.get("location_text") or ""):
        return float(inp["lat"]), float(inp["lng"]), "tag"
    city = (gold.get("city") or "").split(",")[0].strip().lower()
    return (*_CITY[city], "city centre") if city in _CITY else None


def answer_key(rows: list[dict], origin, matches, k: int = 3) -> list[tuple[float, dict, str]]:
    """The k nearest labelled spots that match the request, one per place."""
    found = []
    for r in rows:
        gold = r.get("gold") or {}
        where = located(r)
        if gold.get("in_catalog") and gold.get("venue") and where and matches(gold):
            found.append((_haversine_km(*origin, where[0], where[1]), r, where[2]))
    found.sort(key=lambda t: t[0])
    key: list[tuple[float, dict, str]] = []
    for item in found:
        if not any(venue_same_place(item[1]["gold"]["venue"], kept[1]["gold"]["venue"]) for kept in key):
            key.append(item)
        if len(key) == k:
            break
    return key


def keyword_embedder(texts: list[str]) -> list[list[float]]:
    """Offline stand-in for the embedding model: hashed bag of words, L2-normalised."""
    out = []
    for text in texts:
        vec = [0.0] * 512
        vec[511] = 0.05                       # never all-zero (cosine is undefined there)
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word not in _STOP:
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 511] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        out.append([x / norm for x in vec])
    return out


def build_catalog(rows: list[dict], settings: IngestionSettings, embedder) -> tuple[ChromaSink, dict]:
    """Every reel through the real extraction; what passes validation goes in the catalog."""
    sink = ChromaSink(settings, embedder=embedder, collection_name="location_eval")
    by_hash = {}
    for r in rows:
        record, _reason = build_record(raw_from_input(r["url"], r.get("input") or {}), extractor=None)
        if record is not None:
            by_hash[sink.add(record)] = r
    return sink, by_hash


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--near", default="Laguna Niguel", help="a city in _CITY")
    ap.add_argument("--query", help="one request instead of the defaults (use with --match)")
    ap.add_argument("--match", help="with --query: a dish or word in the venue name that counts as right")
    ap.add_argument("--stand-in", action="store_true", help="offline: keyword embedder, no LLM re-rank")
    args = ap.parse_args()

    origin = _CITY[args.near.lower()]
    queries = ({args.query: _dish_or_name(args.match.lower())} if args.query and args.match
               else {q.format(near=args.near): m for q, m in DEFAULT_QUERIES.items()})
    use_aliases("train")
    rows = [r for r in load_labels() if r.get("gold")]

    with tempfile.TemporaryDirectory() as tmp:
        settings = IngestionSettings(chroma_path=tmp, raw_dir=Path(tmp) / "raw")
        sink, by_hash = build_catalog(rows, settings, keyword_embedder if args.stand_in else None)
        # Geocoder: the city table, mimicking Nominatim on a clean place name. A phrase
        # that isn't a place ("laguna niguel whats good for") finds nothing, as it would live.
        service = RecommendationService(sink, settings, geocode_fn=lambda p: _CITY.get(p.lower().strip(), (None, None)))
        mode = "stand-in keyword embedder, no LLM re-rank" if args.stand_in else f"{settings.embed_model} + LLM re-rank"
        print(f"Catalog: {sink.count()} spots from {len(rows)} labelled reels · retrieval: {mode}\n")

        summary = []
        for n, (query, matches) in enumerate(queries.items()):
            key = answer_key(rows, origin, matches)
            result = service.recommend(f"eval-{n}", query, mode="command", rerank=not args.stand_in)
            recs = result.recommendations if not result.suppressed else []
            place = extract_origin_text(query)
            print(f"━━ {query!r} " + "━" * max(0, 60 - len(query)))
            print(f"   place read as {place!r} → "
                  f"{'found' if place and place.lower() in _CITY else 'not found: no distances'}")
            print("   EXPECTED (answer key)")
            for i, (km, r, how) in enumerate(key, 1):
                g = r["gold"]
                print(f"     {i}. {g['venue']} — {g.get('city')} — {km:.1f} km ({how})")
            print("   SPOTBOT (what the bot posts)")
            text = format_recommendations(recs) if recs else f"(nothing posted: {result.reason})"
            print("     " + text.replace("\n", "\n     "))
            hits = local = 0
            for rec in recs:
                row = by_hash.get(rec.content_hash) or {}
                where = located(row) if row else None
                true_km = _haversine_km(*origin, where[0], where[1]) if where else None
                gold_venue = (row.get("gold") or {}).get("venue")
                hit = any(venue_same_place(gold_venue, r["gold"]["venue"]) for _, r, _ in key)
                hits += hit
                local += true_km is not None and true_km <= 25
                truth = f"{true_km:.1f} km ({where[2]})" if true_km is not None else "location unknown"
                print(f"     ↳ {rec.venue_name}: really {gold_venue or 'no venue'} · {truth}"
                      f"{' · in answer key' if hit else ''}")
            print(f"   SCORE  {hits}/{len(key)} expected spots found · "
                  f"{local}/{len(recs)} results within 25 km\n")
            summary.append((query, hits, len(key), local, len(recs)))

        print("SUMMARY")
        for query, hits, nkey, local, nrecs in summary:
            print(f"  {query:<44} found {hits}/{nkey} expected · {local}/{nrecs} local")


if __name__ == "__main__":
    main()
