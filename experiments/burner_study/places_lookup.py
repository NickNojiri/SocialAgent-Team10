"""A local places lookup built from Overture Maps places (CDLA-Permissive-2.0), for checking
venue names offline. Experiment only.

    python experiments/burner_study/places_lookup.py build      # data/places/*.parquet -> index
    python experiments/burner_study/places_lookup.py "taco land" "Anaheim"

The regions are downloaded with the overturemaps CLI (see the burner-study write-up). The index
keeps named, reasonably confident places: name, squashed name, city, category, lat/lng.
"""
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLACES = REPO / "data" / "places"
INDEX = PLACES / "index.parquet"

# Overture gives every place a category PATH (taxonomy.hierarchy, up to 6 levels, e.g.
# food_and_drink > alcoholic_beverage_venue > bar > cocktail_bar) plus rare alternates. A place
# can be several things, so it maps to EVERY SpotBot category any level (or alternate) implies.
# Checked in order; a level matching nothing contributes nothing. Places with no taxonomy at all
# are kept as "unknown" (one Churrito Loco listing has none).
TO_SPOTBOT = [
    ("alcoholic_beverage_venue", "nightlife"), ("nightlife_venue", "nightlife"), ("brewery", "nightlife"),
    ("winery", "nightlife"), ("social_club", "nightlife"),
    ("non_alcoholic_beverage_venue", "cafe_dessert"), ("coffee_shop", "cafe_dessert"), ("cafe", "cafe_dessert"),
    ("dessert_shop", "cafe_dessert"), ("bakery", "cafe_dessert"), ("ice_cream", "cafe_dessert"),
    ("donut", "cafe_dessert"), ("tea", "cafe_dessert"), ("bubble_tea", "cafe_dessert"), ("juice", "cafe_dessert"),
    ("restaurant", "food_drink"), ("casual_eatery", "food_drink"), ("food_truck", "food_drink"),
    ("food_court", "food_drink"), ("food_and_beverage_store", "food_drink"),
    ("performing_arts_venue", "live_music"), ("music_venue", "live_music"), ("festival_venue", "live_music"),
    ("stadium_arena", "live_music"),
    ("market", "market_popup"), ("event_venue", "community"), ("arts_and_crafts_space", "community"),
    ("park", "outdoors"), ("recreational_trail_or_path", "outdoors"),
    ("gaming_venue", "other"), ("amusement_attraction", "other"), ("movie_theater", "other"),
    ("museum", "other"), ("hotel", "other"), ("resort", "other"),
]


def spotbot_categories(hierarchy, alternates) -> set:
    """Every SpotBot category a place's Overture path + alternates imply ({} = not a venue)."""
    levels = [str(x) for x in list(hierarchy if hierarchy is not None else []) + list(alternates if alternates is not None else [])]
    cats = set()
    for level in levels:
        for key, cat in TO_SPOTBOT:
            if key == level or (key in ("ice_cream", "donut", "tea", "juice", "food_truck") and key in level):
                cats.add(cat)
    # A dessert/coffee spot under casual_eatery is a cafe, not a meal: drop the generic food tag.
    if "cafe_dessert" in cats and not any(l == "restaurant" for l in levels):
        cats.discard("food_drink")
    return cats
# A "place" named only with these words is a description, not a venue. From train/silver false
# fills: 'Restaurants', 'Chinese Food', 'San Diego Desserts', 'POPULAR', 'Regional', 'Good Food'.
GENERIC = {"restaurant", "restaurants", "food", "foods", "dessert", "desserts", "cafe", "coffee", "bakery", "bar",
           "grill", "kitchen", "pizza", "sushi", "tacos", "taco", "ramen", "bbq", "chinese", "japanese", "korean",
           "thai", "mexican", "italian", "vietnamese", "deli", "market", "brunch", "breakfast", "equipment",
           "the", "and", "of", "new", "best", "eats", "spot", "popular", "regional", "good", "sashimi", "menu"}
STREET = re.compile(r"\s*(?:blvd|boulevard|ave|avenue|st|street|dr|drive|rd|road|way|ln|lane|pkwy|parkway)\b", re.I)


def squash(s: str) -> str:
    """Lowercase letters/digits only, accents dropped, '&' read as 'and'."""
    if not isinstance(s, str):
        return ""                                 # missing city / name (NaN in the index)
    s = unicodedata.normalize("NFKD", s.replace("&", " and ")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def build() -> None:
    import duckdb

    srcs = [str(p) for p in PLACES.glob("*.parquet") if p.name not in ("index.parquet",) and not p.name.startswith("test_")]
    con = duckdb.connect()
    con.sql("INSTALL spatial; LOAD spatial;")
    con.sql(f"""
        COPY (
          SELECT names.primary AS name, basic_category AS cat, taxonomy.primary AS cat2,
                 taxonomy.hierarchy AS hierarchy, taxonomy.alternates AS alternates,
                 addresses[1].locality AS city, ST_Y(ST_Centroid(geometry)) AS lat, ST_X(ST_Centroid(geometry)) AS lng,
                 confidence
          FROM read_parquet({srcs!r})
          WHERE names.primary IS NOT NULL AND confidence >= 0.5
        ) TO '{INDEX.as_posix()}' (FORMAT parquet)
    """)
    n = con.sql(f"SELECT count(*) FROM '{INDEX.as_posix()}'").fetchall()[0][0]
    print(f"index: {n} places from {len(srcs)} region file(s) -> {INDEX}")


class Places:
    """In-memory lookup: exact squashed-name hits, fuzzy name matching, and finding known
    place names inside free text (caption, transcript, on-screen text)."""

    def __init__(self, index: Path = INDEX):
        import pandas as pd

        df = pd.read_parquet(index, columns=["name", "cat", "hierarchy", "alternates", "city", "confidence"])
        df["sq"] = df["name"].map(squash)
        df["csq"] = df["city"].map(squash)
        df["spot"] = [spotbot_categories(h, a) for h, a in zip(df["hierarchy"], df["alternates"])]
        unknown = df["hierarchy"].isna() & df["cat"].isna()
        df = df[(df["sq"].str.len() >= 3) & ((df["spot"].map(len) > 0) | unknown)]
        self.df = df.reset_index(drop=True)
        self.by_sq: dict[str, list[int]] = {}
        self.by_city: dict[str, list[int]] = {}
        for i, (s, c) in enumerate(zip(self.df["sq"], self.df["csq"])):
            self.by_sq.setdefault(s, []).append(i)
            self.by_city.setdefault(c, []).append(i)

    def city_rows(self, cities) -> "list[int] | None":
        rows = [i for c in {squash(c) for c in cities or [] if c} for i in self.by_city.get(c, [])]
        return rows or None

    def exact(self, name: str, cities=None) -> "str | None":
        i = self.exact_row(name, cities)
        return None if i is None else self.df.at[i, "name"]

    def exact_row(self, name: str, cities=None) -> "int | None":
        want = {squash(c) for c in cities or [] if c}
        for i in self.by_sq.get(squash(name), []):
            if not want or self.df.at[i, "csq"] in want:
                return i
        return None

    def categories(self, name: str, cities=None) -> set:
        """SpotBot categories of the place with this name in these cities (all its listings)."""
        want = {squash(c) for c in cities or [] if c}
        cats = set()
        for i in self.by_sq.get(squash(name), []):
            if not want or self.df.at[i, "csq"] in want:
                cats |= self.df.at[i, "spot"]
        return cats

    def fuzzy(self, name: str, cities, min_score: float = 90) -> "tuple[str, float] | None":
        """Best name match among places in the given cities (never region-wide: too many lookalikes)."""
        from rapidfuzz import fuzz, process

        rows = self.city_rows(cities)
        if not rows or not squash(name):
            return None
        choices = {i: self.df.at[i, "sq"] for i in rows}
        hit = process.extractOne(squash(name), choices, scorer=fuzz.ratio, score_cutoff=min_score)
        return (self.df.at[hit[2], "name"], hit[1]) if hit else None

    def prefix(self, name: str, cities, min_len: int = 6) -> "str | None":
        """The longest known place whose name is the START of `name`, e.g. 'Tacolandofficial' or
        'Churritoloco Southgate' -> 'Taco Land' / 'Churrito Loco'. Only places in the given
        cities count; with no city, only long (>= 9 letters) prefixes count, region-wide."""
        q = re.sub(r"(official|usa|hq)$", "", squash(name))
        want = {squash(c) for c in cities or [] if c}
        for n in range(len(q), min_len - 1, -1):
            rest = q[n:]
            # What's left after the known name must be a location or a tag ('lakewood', 'dtla',
            # '180'), never more name ('Balboa Island' + 'bakingco' is a different listing).
            if rest and not (len(rest) <= 4 or rest.isdigit() or rest in self.by_city):
                continue
            for i in self.by_sq.get(q[:n], []):
                if self.df.at[i, "csq"] in want or (not want and n >= 9):
                    return self.df.at[i, "name"]
        return None

    def is_generic(self, name: str) -> bool:
        words = re.findall(r"[a-z]+", (name or "").lower())
        return all(w in GENERIC or squash(w) in self.by_city for w in words) or squash(name) in self.by_city

    def names_in_text(self, text: str, cities, min_len: int = 6) -> list[str]:
        """Place names (from the given cities) that appear word-for-word in the text, longest first."""
        want = {squash(c) for c in cities or [] if c}
        if not want:
            return []
        text = text or ""
        spans = [(m.group(), m.end()) for m in re.finditer(r"[\w'&’]+", text)]
        words = [w for w, _ in spans]
        found = {}
        for n in range(5, 0, -1):
            for i in range(len(words) - n + 1):
                sq = squash(" ".join(words[i:i + n]))
                if len(sq) < min_len or sq in found:
                    continue
                ws = words[i:i + n]
                # Written like a name: first and last word capitalized (or digits), not a lone
                # shouted word ('POPULAR'), and not the start of a street ('Abbot Kinney Blvd').
                if not all(w[:1].isupper() or w[:1].isdigit() for w in (ws[0], ws[-1])):
                    continue
                if n == 1 and ws[0].isupper():
                    continue
                if STREET.match(text[spans[i + n - 1][1]:]):
                    continue
                for j in self.by_sq.get(sq, []):
                    if self.df.at[j, "csq"] in want and not self.is_generic(self.df.at[j, "name"]):
                        found[sq] = self.df.at[j, "name"]
                        break
        return sorted(found.values(), key=len, reverse=True)


@lru_cache(maxsize=1)
def places() -> Places:
    return Places()


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build()
    else:
        p = places()
        name, city = sys.argv[1], sys.argv[2:3]
        print("exact:", p.exact(name, city), "| fuzzy:", p.fuzzy(name, city, 70))
