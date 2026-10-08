"""Field candidates from a raw snapshot: heuristic baseline, optionally refined by the LLM.

Two pure entry points (no I/O, no network — the LLM call lives in llm_extractor):
  - build_llm_payload(raw): clean, capped JSON payload for the model.
  - normalize(raw, llm_extraction=None): candidate kwargs for EventInspiration.

The heuristic baseline is always computed; when an LlmExtraction is supplied its
grounded fields override the heuristics, while coordinates, hashtags, hashes, and
provenance stay code-owned (merged, never delegated).
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from src.ingestion.schemas.extraction import LlmExtraction
from src.ingestion.schemas.inspiration import EventCategory, GeoContext, GeoSource
from src.ingestion.schemas.snapshot import RawPostSnapshot

# Scored, not first-match: every keyword hit is a vote, and the category with the
# most votes wins (see `_categorize` for ties). First-match let one "cake" turn a
# pasta restaurant into a dessert spot and one "bar" turn "Sei Pizza Bar" into
# nightlife. Plurals are matched by `_kw_regex`.
_CATEGORY_KEYWORDS: list[tuple[EventCategory, tuple[str, ...]]] = [
    (EventCategory.CAFE_DESSERT, (
        "cafe", "café", "coffee", "espresso", "latte", "dessert", "boba", "bubble tea",
        "bakery", "bakeries", "pastry", "pastries", "croissant", "ice cream", "gelato",
        "matcha", "cake", "cheesecake", "cupcake", "donut", "doughnut", "churro", "crepe",
        "s'more", "smore", "fresas con crema", "cookie", "brownie", "macaron", "mochi",
        "tiramisu", "flan", "pudding", "cinnamon roll", "froyo", "frozen yogurt",
        "soft serve", "shaved ice", "bingsu", "milk tea", "tea house", "teahouse",
        "patisserie", "panaderia", "creamery", "chocolate", "pie", "tart", "sweets",
        "pumpkin spice", "pan dulce", "concha", "postre", "postres", "affogato",
        "cold brew", "chai", "slushy", "smoothie", "acai",
    )),
    (EventCategory.LIVE_MUSIC, (
        "live music", "concert", "band", "dj set", "dj ", "jazz", "open mic",
        "vinyl night", "gig", "live set", "house music", "rave",
    )),
    (EventCategory.MARKET_POPUP, (
        "pop-up", "popup", "pop up", "night market", "food fair", "farmers market",
        "festival", "vendor", "vendors", "booth", "all weekend", "weekend only",
        "one day only", "this weekend only", "market this",
    )),
    (EventCategory.NIGHTLIFE, (
        "bar", "cocktail", "cocktails", "brewery", "brew", "club", "happy hour",
        "speakeasy", "wine bar", "natural wine", "lounge", "nightclub",
    )),
    (EventCategory.OUTDOORS, (
        "hike", "hiking", "hiking trail", "trailhead", "kayak", "kayaking",
        "camping", "campground", "national park", "state park", "national forest",
        "tide pool", "tide pools", "beach day", "beach cleanup", "beach bonfire",
        "picnic in", "to the beach", "at the beach",
    )),
    (EventCategory.COMMUNITY, ("meetup", "community", "volunteer", "workshop", "book club", "class ", "market day")),
    (EventCategory.FOOD_DRINK, (
        "taco", "birria", "restaurant", "restaurante", "food", "brunch", "breakfast",
        "lunch", "dinner", "eats", "eatery", "kitchen", "grill", "diner", "bistro",
        "ramen", "sushi", "omakase", "poke", "bbq", "barbecue", "korean bbq", "kbbq",
        "pizza", "pizzeria", "burger", "burrito", "quesadilla", "nachos", "carne asada",
        "al pastor", "pho", "noodle", "dumpling", "dim sum", "hot pot", "hotpot", "naan",
        "curry", "pastrami", "sandwich", "deli", "wings", "fried chicken", "crispy pata",
        "steak", "seafood", "oyster", "lobster", "crab", "dessert menu", "menu", "erewhon",
        "pasta", "spaghetti", "carbonara", "lasagna", "gnocchi", "trattoria", "osteria",
        "fries", "rice", "teriyaki", "katsu", "udon", "soba", "shabu", "yakitori",
        "izakaya", "gyoza", "bao", "banh mi", "tamale", "torta", "enchilada", "empanada",
        "arepa", "ceviche", "taqueria", "kebab", "shawarma", "falafel", "gyro", "halal",
        "chicken", "beef", "pork", "wagyu", "brisket", "ribs", "shrimp", "salmon",
        "fish", "hot dog", "sausage", "salad", "soup", "fritter", "mac and cheese",
        "buffet", "ayce", "all you can eat", "bento", "chef", "cuisine", "dining",
        "reservation", "appetizer", "entree", "bowl", "yakiniku", "gyutan", "tonkatsu",
        "mashed potato", "potato", "bread", "pupusa", "mole", "pozole", "menudo",
    )),
]

# Generic words count a quarter vote: they show the post is about food without
# saying which kind, and dessert accounts use them as much as restaurants do.
_GENERIC_FOOD = {"food", "eats", "menu", "chef", "dining", "cuisine", "kitchen", "bread"}
_GENERIC_WEIGHT = 0.25

# The kind of place outranks what was eaten there: a café that serves breakfast is
# still a café, and a bar with a burger is still a bar. When the caption names a
# place type, the most-named type wins; dish words only decide when it names none.
_PLACE_TYPES: list[tuple[EventCategory, tuple[str, ...]]] = [
    (EventCategory.CAFE_DESSERT, (
        "cafe", "café", "coffee shop", "coffeehouse", "coffee", "bakery", "bakeries",
        "patisserie", "panaderia", "creamery", "tea house", "teahouse", "dessert shop",
        "donut shop", "boba shop", "ice cream shop", "gelateria", "chocolatier",
    )),
    (EventCategory.LIVE_MUSIC, ("concert", "live music", "open mic", "dj set", "live set")),
    (EventCategory.MARKET_POPUP, (
        "pop-up", "popup", "pop up", "night market", "farmers market", "food fair",
        "festival", "vendors", "flea market",
    )),
    (EventCategory.NIGHTLIFE, (
        "bar", "brewery", "speakeasy", "lounge", "nightclub", "wine bar", "cocktail bar",
        "pub", "taproom", "happy hour",
    )),
    (EventCategory.OUTDOORS, ("hike", "hiking", "trailhead", "national park", "state park",
                              "campground")),
    (EventCategory.COMMUNITY, ("meetup", "volunteer", "workshop", "book club")),
    (EventCategory.FOOD_DRINK, (
        "restaurant", "restaurante", "pizzeria", "trattoria", "osteria", "taqueria",
        "bistro", "diner", "eatery", "izakaya", "steakhouse", "buffet", "food truck",
        "food hall",
    )),
]

# A food word in front of "bar" names a restaurant, not nightlife ("Sei Pizza Bar",
# "Yuzu Sushi Bar", "@pvdnoodlebar"); a drink word in front of it is a cafe.
_FOOD_BAR = re.compile(
    r"\b(pizza|sushi|noodle|ramen|pasta|hand ?roll|oyster|raw|taco|poke|salad|burger|crudo|"
    r"kitchen|restaurant|espresso|coffee|matcha|dessert|juice|smoothie|acai|boba|tea)"
    r"(?:[\s-]?|\s+(?:and|&)\s+)bar\b",
    re.IGNORECASE,
)

_CATEGORY_EMOJI: dict[EventCategory, str] = {
    EventCategory.CAFE_DESSERT: "🍦🍨🍧🍰🎂🧁🍩🍪🍫🍬🍭🍮☕🍵🧋🥐🥧🍡",
    EventCategory.FOOD_DRINK: "🍕🍔🍟🌭🌮🌯🍜🍝🍣🍱🍛🍲🥘🍖🍗🥩🍤🥟🍙🍚🥗🥪🍳🥞🦞🦀🥢🫕🥙🧆🫔",
    EventCategory.NIGHTLIFE: "🍸🍹🍺🍻🍷🥂🥃🪩",
    EventCategory.LIVE_MUSIC: "🎵🎶🎤🎸🎧🥁🎷🎺",
}

_PIN_LINE = re.compile(r"📍\s*(?P<loc>[^\n#@—!|:]+)")
_PIN_LEAD = re.compile(r"^(?:new!?\s*|find\s+|now open(?:\s+at)?\s+|check out\s+|try\s+)", re.IGNORECASE)
_PIN_SENTENCE = re.compile(r"(?:\.\s|\s{2,}| is | are | just | located | serves? | opens? | where )")
# Food-blogger standard: an optional emoji, the venue name, then " — City" /
# " | City" / " - City". Captured before the dash. Anchored near the caption
# start (first ~2 lines) so a mid-caption dash doesn't trigger it.
_NAME_DASH_CITY = re.compile(
    r"^(?:\s*[^\w\s#@]{0,4}\s*)?"                       # leading emoji(s), optional
    r"(?P<venue>[A-Za-z][\w'’&.\-]*(?:\s+[A-Za-z'’&.\-]+){0,4}?)\s*"
    r"[—–\-|]\s+"
    r"(?P<city>[A-Za-z][\w'’.\-]*(?:[ ,]+[A-Za-z][\w'’.\-]*){0,3})",
)
# "[ naisnow ] monterey park , ca" / "(frankie greek yogurt) los angeles, ca"
_BRACKET_CITY = re.compile(
    r"^\s*[\[(]\s*(?P<venue>[^\]\)\n|]{2,40}?)\s*[\])]\s*"
    r"(?P<city>[A-Za-z][\w'’.\-]*(?:[ ,]+[A-Za-z][\w'’.\-]*){0,3})",
)
_BRACKET_STOP = {"closed", "ad", "sponsored", "paid", "collab", "gifted", "invited"}
# "... it's called X" / "a spot called X" / "named X" — venue named after the vibe.
_CALLED = re.compile(
    r"\b(?:called|named)\s+(?P<venue>[A-Z][\w'’&.\-]*(?:\s+[A-Z][\w'’&.\-]*){0,4})"
)
# Recipe / brand-content / tourism-board posts — not a specific place.
_RECIPE_RE = re.compile(
    r"\b(recipe|ingredients?|ingredientes|receta|here'?s how|step 1|preheat|"
    r"tbsp|tsp|mix (?:together|in)|stir until|procedimiento|precalienta|precalentar|"
    r"amasa|hornea|cucharad(?:a|ita)|modo de preparaci[oó]n|\bmezcla\b)\b",
    re.IGNORECASE,
)
_TOURISM_HANDLE = re.compile(r"^(visit|discover|explore|experience|see|go)[a-z]", re.IGNORECASE)
_COMMENT_BAIT = re.compile(r'\bcomment\s+["“]?[A-Z]{2,}', re.IGNORECASE)
# Countries / macro-regions / big neighborhoods — never a venue on their own.
_REGION_STOP = {
    "japan", "korea", "south korea", "china", "thailand", "thai", "vietnam", "mexico",
    "italy", "france", "spain", "taiwan", "hong kong", "philippines", "india", "brazil",
    "tokyo", "kyoto", "uji", "seoul", "saga", "shanghai", "bay area", "the bay",
    "east bay", "west adams", "sawtelle", "koreatown", "convoy", "melrose",
    "socal", "southern california", "orange county", "los angeles",
}
_CITY_COMMA_ST = re.compile(r",\s*(?:ca|california|[a-z]{2})\b", re.IGNORECASE)
_AT_VENUE = re.compile(r"\bat\s+(?:the\s+)?(?P<venue>[A-Z][\w'’&-]*(?:\s+[A-Z][\w'’&-]*){0,4})")
# A 📍 line that IS the venue: 2–5 Title-case tokens, no digits, ends in a venue
# category word (or letter-matches an @mention — checked in _venue_slot).
_PIN_VENUE_KW = re.compile(
    r"\b(cafe|café|coffee|coffeehouse|bakery|kitchen|bar|house|grill|pizzeria|"
    r"restaurant|creamery|tea|teahouse|deli|club|lounge|eatery|tavern|bistro|"
    r"parlor|parlour|market|shop|co)$", re.IGNORECASE,
)
# A street address, not a venue name: leading number, or a street-type token.
_ADDRESS_RE = re.compile(
    r"^\s*\d{1,6}\s+\S|"
    r"\b\d{1,6}\s+[\w.]+\s+(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|"
    r"way|ln|lane|ct|court|hwy|pkwy|pl|place)\b",
    re.IGNORECASE,
)
# "from / by <Venue>" and "from / by @handle" — the venue-mention slot. Handled
# separately from `at` so a preposition-specific priority is possible and so the
# @handle branch can survive (an @mention has no leading capital).
_FROM_VENUE = re.compile(
    r"\b(?:from|by)\s+(?P<venue>[A-Z][\w'’&\-]*"
    r"(?:\s+(?:de|del|la|le|du|of|the|and|&)\b)*"
    r"(?:\s+[A-Z][\w'’&\-]*){0,4})"
)
# "steps from X" / "minutes away from X" names a landmark nearby, not the venue.
_NEAR_LANDMARK = re.compile(
    r"\b(?:steps|minutes?|mins?|blocks?|miles?|away|far|across|walking distance|right)\s+(?:from|by)\s*$",
    re.IGNORECASE,
)
# A person, not a venue, when the mention starts with one of these.
_PERSON_LEAD = re.compile(r"^(chef|owner|founder|my|the|our|host|dj)\b", re.IGNORECASE)
_OWNER_OF = re.compile(r"\bowner of\s+@(?P<handle>[a-zA-Z0-9_.]{2,30})")
# A Title-Case proper name in real double quotes — usually a pop-up / event /
# branded item. Apostrophes/single quotes excluded (they match "she's back").
_QUOTED_NAME = re.compile(r"[\"“”]([A-Z][\w&'’.-]*(?:\s+[\w&'’.-]+){0,4})[\"“”]")
_FROM_HANDLE = re.compile(r"\b(?:from|by|at)\s+@(?P<handle>[a-zA-Z0-9_.]{2,30})")
_ANY_HANDLE = re.compile(r"(?<![\w.])@(?P<handle>[a-zA-Z0-9_][a-zA-Z0-9_.]{1,29})")
# Food-blogger / reviewer handles — a mention of one is not a venue.
_BLOGGER_HANDLE = re.compile(
    r"(eats|eatz|eater|foodie|foodies|foodiego|noms|munch|dine|dining|plates|"
    r"hungry|bites|cravings|tastes|reviews|guide|list|feed)$", re.IGNORECASE,
)
# First person POSSESSIVE of the place → the posting account is the venue.
# Deliberately narrow: "we've been waiting for <other venue>" must NOT match.
_FIRST_PERSON = re.compile(
    r"\bour\s+(?:\w+\s+){0,2}"
    r"(?:menu|bar|patio|shop|store|cafe|café|kitchen|location|spot|place|team|doors|"
    r"opening|bakery|restaurant|counter|window|stand|booth|kiosk|truck|cart|famous)\b"
    r"|\bour\s+new\s+\w+|\bour\s+\w+\s+location\b"
    r"|\bcome (?:to|see|visit) (?:us|our)\b|\bvisit us\b|\bwe'?re (?:open|now open|back)\b"
    r"|\bwelcome to\b|\bjoin us for our\b|\byou('?ve| have) (?:tried|been to) us\b"
    r"|\badd(?:ed)? (?:this )?to (?:our|the) menu\b|\bthere('?s| is) always a seat\b",
    re.IGNORECASE,
)
# Named complexes that are never the venue on their own — kept as context.
_CONTAINERS = {
    "disney", "disneyland", "disneyland resort", "disney world", "disney springs",
    "walt disney world", "california adventure", "disney california adventure",
    "universal", "universal studios", "universal studios hollywood",
    "universal citywalk", "citywalk", "knott's berry farm", "knotts berry farm",
    "the grove", "the mall", "the airport", "lax", "sfo",
}
_IN_CITY = re.compile(r"\b[Ii]n\s+(?P<city>[A-Z][a-zA-Z'-]*(?:\s+[A-Z][a-zA-Z'-]*){0,2})")

# In-house area gazetteer (seed: SoCal — the product's roots). A hashtag whose
# normalised form equals a key, or contains one as a substring after stripping a
# food/travel suffix ("longbeacheats" -> "longbeach"), yields the place.
# Grow this file from /dash corrections — do not reach for a geocoding API.
_AREA_HASHTAGS = {
    "losangeles": "Los Angeles", "dtla": "Downtown Los Angeles", "socal": "Southern California",
    "longbeach": "Long Beach", "lbc": "Long Beach",
    "orangecounty": "Orange County", "theoc": "Orange County",
    "anaheim": "Anaheim", "santaana": "Santa Ana", "irvine": "Irvine", "fullerton": "Fullerton",
    "costamesa": "Costa Mesa", "huntingtonbeach": "Huntington Beach", "gardengrove": "Garden Grove",
    "riverside": "Riverside", "sandiego": "San Diego", "pasadena": "Pasadena",
    "sangabrielvalley": "San Gabriel Valley", "sgv": "San Gabriel Valley",
    "sanfrancisco": "San Francisco", "hollywood": "Hollywood",
    "koreatown": "Koreatown", "ktown": "Koreatown", "carson": "Carson", "torrance": "Torrance",
    "alhambra": "Alhambra", "montereypark": "Monterey Park", "cerritos": "Cerritos",
    "newportbeach": "Newport Beach", "danapoint": "Dana Point", "sanjuancapistrano": "San Juan Capistrano",
    "sanmarcos": "San Marcos", "escondido": "Escondido", "carlsbad": "Carlsbad",
    "ranchocucamonga": "Rancho Cucamonga", "redlands": "Redlands", "rollinghillsestates": "Rolling Hills Estates",
    "downey": "Downey", "westadams": "West Adams", "sawtelle": "Sawtelle", "westhollywood": "West Hollywood",
    "santamonica": "Santa Monica", "culvercity": "Culver City", "melrose": "Melrose",
    # outside SoCal — appears in DM batches
    "sanjose": "San Jose", "sanmateo": "San Mateo", "berkeley": "Berkeley", "hayward": "Hayward",
    "paloalto": "Palo Alto", "bayarea": "Bay Area", "sacramento": "Sacramento",
    "chicago": "Chicago", "logansquare": "Logan Square", "lakeview": "Lakeview",
    "seattle": "Seattle", "newyork": "New York", "nyc": "New York", "hellskitchen": "Hell's Kitchen",
    "williamsburg": "Williamsburg", "brooklyn": "Brooklyn", "queens": "Queens", "littleneck": "Little Neck",
    "boston": "Boston", "dallas": "Dallas", "orlando": "Orlando", "winterpark": "Winter Park",
    "miami": "Miami", "wynwood": "Wynwood", "coralgables": "Coral Gables", "fortlauderdale": "Fort Lauderdale",
    "toronto": "Toronto", "yorkville": "Yorkville", "vancouver": "Vancouver",
    "melbourne": "Melbourne", "perth": "Perth", "subiaco": "Subiaco", "amsterdam": "Amsterdam",
    "sydney": "Sydney", "chinatown": "Chinatown", "singapore": "Singapore",
    # named complexes that pin a city
    "disneyland": "Anaheim", "disneylandresort": "Anaheim", "downtowndisney": "Anaheim",
    "disneysprings": "Lake Buena Vista", "waltdisneyworld": "Lake Buena Vista",
    "universalstudioshollywood": "Universal City", "universalcitywalk": "Universal City",
}
_HASHTAG_SUFFIXES = ("eats", "eater", "eatery", "food", "foodie", "foodies", "eeeeeats",
                     "restaurants", "coffee", "life", "living", "local", "vibes", "explore")
# Lowercased gazetteer place names — used to reject a "venue" that is really a city.
_AREA_CITY_SET = {v.lower() for v in _AREA_HASHTAGS.values()}
_TITLE_NOISE = re.compile(r"\(@[\w.]+\)")  # "(handle)" suffixes in account titles
_URL = re.compile(r"https?://\S+")
_TIME_MENTION = re.compile(
    r"(?i)\b("
    r"(?:this\s+|next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|tonight|tomorrow|this\s+weekend"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm)"
    r")\b"
)

# Payload caps so a giant caption can't blow the model's context window.
_MAX_CAPTION = 1500
_MAX_TRANSCRIPT = 1500
_MAX_TITLE = 200
_MAX_DESC = 500
_MAX_HASHTAGS = 15


def build_llm_payload(raw: RawPostSnapshot) -> dict:
    """Compact, URL-stripped, length-capped payload — only fields worth judging."""
    payload: dict = {"platform": raw.platform}

    caption = _clean(raw.caption)
    if caption:
        payload["caption"] = caption[:_MAX_CAPTION]
    title = (raw.title or "").strip()
    if title:
        payload["title"] = title[:_MAX_TITLE]
    description = _clean(raw.description)
    if description and description != caption:  # avoid feeding the same text twice
        payload["og_description"] = description[:_MAX_DESC]
    transcript = _clean(raw.transcript)
    if transcript:  # spoken venue/location the caption may omit (Phase 2.5)
        payload["transcript"] = transcript[:_MAX_TRANSCRIPT]
    frame_text = _clean(getattr(raw, "frame_text", None))
    if frame_text:  # burned-in on-screen text the caption may omit (Phase 2.6)
        payload["on_screen_text"] = frame_text[:_MAX_DESC]
    if raw.location_text:
        payload["location_text"] = raw.location_text.strip()
    if raw.hashtags:
        payload["hashtags"] = raw.hashtags[:_MAX_HASHTAGS]
    if raw.fetched_at:  # context only; the prompt forbids copying it into times
        payload["today_date"] = raw.fetched_at.date().isoformat()

    return payload


def normalize(raw: RawPostSnapshot, llm_extraction: Optional[LlmExtraction] = None) -> dict:
    """Return candidate kwargs for EventInspiration (validation happens later)."""
    caption = raw.caption or ""
    # Transcript is deliberately NOT in `searchable`: conversational speech
    # ("the bar was packed", "we hiked after") trips category keywords. It's a
    # venue/location signal only — used narrowly in the slot fallback below.
    searchable = " ".join(
        filter(None, [raw.caption, raw.frame_text, raw.title, raw.description,
                      " ".join(raw.hashtags or [])])
    )

    # 1. Heuristic baseline — always computed, deterministic, cheap.
    venue, venue_slot = _venue_slot(raw, caption)
    if venue is None and raw.transcript:
        # spoken-only venue: only trust a high-signal slot from the transcript —
        # a run-together @handle, "from/by X", or "called X". "at X" / quoted from
        # conversational speech is too noisy.
        t_venue, t_slot = _venue_slot(raw, raw.transcript)
        if t_venue and t_slot in ("handle_from", "from_titlecase", "quoted", "jsonld"):
            venue, venue_slot = t_venue, "transcript"
    if venue and (fix := _ALIASES.get(_norm_alias(venue))):   # learned correction
        venue, venue_slot = fix, "alias"
    elif venue and venue_slot != "jsonld":
        venue = _trim_venue(venue)
        if fix := _ALIASES.get(_norm_alias(venue)):
            venue, venue_slot = fix, "alias"
    geo = _geo_context(raw, caption if not raw.transcript else f"{caption}\n{raw.transcript}")
    if venue is None and geo.raw_location_text:
        guess = geo.raw_location_text.split(",")[0].strip() or None
        if guess and not _ADDRESS_RE.search(guess) and not _is_container(guess):
            venue, venue_slot = guess, "at_venue"  # weak: a bare location string
    candidate_times = _heuristic_times(searchable, raw.start_date_raw)
    category = _categorize(
        " ".join(filter(None, [raw.caption, raw.frame_text, raw.title, raw.description])),
        raw.hashtags,
    )
    core_theme = _core_theme(raw, caption)

    # 2. LLM layer — a GAP-FILLER, not an overrider. The slot parser + gazetteer
    #    now beat a small local model on this content (measured: see
    #    docs/EXTRACTION_ACCURACY.md), so the model only supplies what the
    #    heuristics left blank. It never overrides a confident heuristic slot,
    #    and structured data (JSON-LD Place) always wins.
    if llm_extraction is not None:
        ext = llm_extraction
        if raw.venue_candidate:
            venue, venue_slot = raw.venue_candidate.strip(), "jsonld"
        elif venue is None and ext.venue_name:
            venue, venue_slot = ext.venue_name, "llm_fill"
        core_theme = core_theme or ext.core_theme
        # Category stays with the keyword heuristic. Measured: the small local
        # model turns a correct "other" (promo / vague posts) into a wrong
        # specific label more often than it rescues a genuine miss.
        candidate_times = _dedupe([*candidate_times, *ext.candidate_times])
        # Only consult the model for location when the heuristics found nothing.
        if geo.source is GeoSource.NONE and not geo.place_names:
            geo = _merge_geo(geo, ext)

    if venue:
        venue = venue[:110].strip()

    is_vague = looks_vague(raw, venue, geo, category, candidate_times, venue_slot)
    # A real place whose kind the text never names: food & drink is by far the most
    # common kind in the labeled corpus, so it is a better guess than "other". Set
    # after `is_vague`, which reads a bare OTHER as "nothing here points to a place".
    if category is EventCategory.OTHER and venue and not is_vague:
        category = EventCategory.FOOD_DRINK

    image_url = (raw.image_url or "").strip()

    return {
        "venue_name": venue,
        "core_theme": core_theme,
        "category": category,
        "geo": geo,
        "hashtags": raw.hashtags,
        "candidate_times": candidate_times,
        # Code-owned like coordinates — the LLM never supplies or overrides it.
        "image_url": image_url if image_url.startswith("http") else None,
        # Which slot filled the venue + its confidence (0-1). Routes the uncertain
        # tail to review and, later, targets LLM spend at the low-confidence rows.
        "venue_slot": venue_slot,
        "venue_confidence": VENUE_CONF[venue_slot] if venue else 0.0,
        # Not an EventInspiration field — build_record pops it and rejects if set.
        "is_vague": is_vague,
    }


# ── heuristic helpers (Phase 1, unchanged behavior) ─────────────────────────


def _handle_to_name(handle: str) -> str:
    """@waterfall.chicken -> 'Waterfall Chicken' (separators), and
    @waterfallchicken -> 'Waterfall Chicken' via the bundled word-split; an
    unsegmentable run stays whole ('erewhon' -> 'Erewhon')."""
    from src.ingestion.pipeline.handle_split import split_handle

    return split_handle(handle)


def _is_container(name: Optional[str]) -> bool:
    return bool(name) and name.strip().lower().rstrip(".").strip() in _CONTAINERS


_DISPLAY_JUNK = re.compile(
    r"\b(foodie|eats|eater|eatz|noms|blog|guide|reviews?|official|nyc|la|oc|sd|sf|"
    r"restaurant group|hospitality|media|content)\b", re.IGNORECASE,
)


def _clean_display_name(name: Optional[str]) -> Optional[str]:
    """An IG profile 'full_name' — a venue name only if it reads like one: a short
    Title-Case phrase with no blogger/personal-account tells and no emoji."""
    if not name:
        return None
    s = re.sub(r"[^\w &'’.\-]", "", name).strip(" .,&-")
    if not s or len(s) > 45 or "  " in name.strip():
        return None
    toks = s.split()
    if not (1 <= len(toks) <= 5) or _DISPLAY_JUNK.search(s):
        return None
    if not any(t[:1].isupper() for t in toks) or s.lower() in _AREA_CITY_SET:
        return None
    return s


# Which slot filled the venue → a confidence the rest of the system can act on
# (route low-confidence to human review; spend the LLM only on the uncertain tail).
VENUE_CONF = {
    "alias": 0.9, "jsonld": 0.95, "handle_from": 0.85, "first_person": 0.8, "quoted": 0.7,
    "from_titlecase": 0.7, "bare_mention": 0.6, "at_venue": 0.55, "transcript": 0.5,
    "title_fallback": 0.3, "llm_fill": 0.35, "list": 0.0, "none": 0.0,
    "pin": 0.8, "name_line": 0.75, "sentence": 0.6,
}

# Learned corrections: normalize(predicted) -> canonical venue. Regenerate with
# scripts/build_aliases.py after any change to fixtures/labels.jsonl.
#
# The default table is built from the WHOLE corpus, which is right for the product
# (a user's correction should stick) and wrong for scoring: a test row's own gold
# label ends up compiled into the extractor that scores it. `eval.py` therefore
# swaps in the train-only table via `reload_aliases()` before reporting held-out
# numbers — see docs/ML_REVIEW_QUESTIONS.md finding 1.
_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
ALIASES_PATH = Path(os.getenv("VENUE_ALIASES_PATH") or _FIXTURES / "venue_aliases.json")

_ALIASES: dict[str, str] = {}


def reload_aliases(path: Optional[Path] = None) -> int:
    """Point the alias table at `path` (default: `ALIASES_PATH`). Returns its size."""
    global _ALIASES
    try:
        _ALIASES = json.loads(Path(path or ALIASES_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing/!unreadable table just means no overrides
        _ALIASES = {}
    return len(_ALIASES)


reload_aliases()


def _norm_alias(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_VENUE_COLON = re.compile(r"^\s*(?P<venue>[A-Z][\w &'’.\-]{2,45}?):\s*\$")


def _is_region(name: Optional[str]) -> bool:
    """A country / macro-region / bare 'City, ST' — not a specific venue."""
    if not name:
        return False
    low = name.strip().strip(" .,").lower()
    return low in _REGION_STOP or low in _AREA_CITY_SET or bool(_CITY_COMMA_ST.search(name))


def _titlecase_if_flat(s: str) -> str:
    return s.title() if (s.islower() or s.isupper()) else s


def _clean_venue_tag(text: str) -> Optional[str]:
    """A platform/JSON-LD location tag is often 'Name 1234 Some St', 'Name - City',
    or just an address. Keep the name, drop a pure address."""
    s = text.strip()
    s = re.sub(r"\s+[-–—]\s+[A-Z][\w' ]+$", "", s)   # trailing ' - Laguna Beach'
    m = re.search(r"\s\d{1,6}\s", s)                  # split before the address run
    if m:
        head = s[: m.start()].strip(" ,·-")
        if head and not _ADDRESS_RE.search(head):
            return head
    return None if _ADDRESS_RE.search(s) else s or None


def _fuller_form(name: str, caption: str) -> str:
    """'1954 Bakery' (from @1954bakery) → '1954 Egashira Bakery' when the caption
    writes the name out with the same first and last word and more in between."""
    toks = name.split()
    if len(toks) < 2:
        return name
    first, last = re.escape(toks[0]), re.escape(toks[-1])
    m = re.search(rf"\b({first}(?:\s+[A-Z0-9][\w'’&.\-]*){{1,3}}\s+{last})\b", caption, re.IGNORECASE)
    if m and not m.group(1)[:1].islower() and len(m.group(1).split()) > len(toks):
        return m.group(1)
    return name


def _caption_spelling(name: str, caption: str) -> str:
    """A run-together @handle name ('Hironoriramen') → the caption's real spacing
    ('Hironori Craft Ramen') when the same letters appear there as words."""
    target = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(target) < 5:
        return name
    words = caption.split()
    for n in range(1, 6):
        for i in range(len(words) - n + 1):
            span = " ".join(words[i : i + n])
            if "@" in span or "#" in span:
                continue                       # the handle/tag itself, not prose
            if re.sub(r"[^a-z0-9]", "", span.lower()) == target:
                span = re.sub(r"^[\W_]+", "", span).strip(" .,!?:;\"'()")
                return _titlecase_if_flat(span)
    return name


def _name_dash_city(caption: str) -> tuple[Optional[str], Optional[str]]:
    """'🍕 Folks Pizzeria — Culver City, LA' → ('Folks Pizzeria', 'Culver City').
    Also handles '[ naisnow ] monterey park, ca' and lowercase venue heads."""
    for line in caption.splitlines()[:2]:
        line = line.strip()
        for rx in (_NAME_DASH_CITY, _BRACKET_CITY):
            m = rx.match(line)
            if not m:
                continue
            venue = m.group("venue").strip(" .,!?:;-–—")
            city = m.group("city").split(",")[0].strip()
            if venue.lower() in _BRACKET_STOP or len(venue) < 3 or _is_container(venue):
                continue
            # a lowercase/all-caps head is only trusted when the city half looks real
            if (venue.islower() or venue.isupper()) and not (
                _CITY_COMMA_ST.search(m.group("city")) or _is_region(city)
            ):
                continue
            return _titlecase_if_flat(venue), (city or None)
    return None, None


_LIST_PHRASE = re.compile(
    r"\b(top\s?\d+|\d+\s+best|best\s+\d+|my\s+(?:top|favou?rite)\s+\d+|ranked|tier\s?list|"
    r"\d+\s+(?:spots?|places?|restaurants?|cafes?|bars?|bakeries)\s+(?:in|to|for|you)|"
    r"bucket\s?list|current\s+rankings?|places?\s+to\s+try\s+in)\b",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^\s*(?:\d{1,2}[.):]\s|[①-⑳]|[-•*✅]\s*[A-Z]|🥇|🥈|🥉)", re.MULTILINE)


def _looks_like_list(raw: RawPostSnapshot, caption: str) -> bool:
    """A ranking / roundup of several venues — there's no single answer."""
    author = (getattr(raw, "author_handle", "") or "").lstrip("@").lower()
    mentions = {h.lower() for h in _ANY_HANDLE.findall(caption) if h.lower() != author}
    items = len(_LIST_ITEM.findall(caption))
    pins = caption.count("📍")
    if _LIST_PHRASE.search(caption) and (items >= 2 or len(mentions) >= 3 or pins >= 3):
        return True
    return items >= 4 or len(mentions) >= 5 or pins >= 4


_PIN_LABEL = re.compile(
    r"^\s*(?:location|address|where|find us|located(?:\s+at)?)\s*[:\-–]\s*", re.IGNORECASE)
_NAME_TAIL = re.compile(r"\s*\([^)]*\)?\s*$")                 # 'Name (Neighbourhood)'
_NAME_SPLIT = re.compile(r"\s[|•·]\s?|\s[-–—]\s")             # 'Name - City', 'Name | Bakery'
_IN_PLACE_TAIL = re.compile(r"\s+(?:in|inside)\s+(?!the\b)[A-Z].*$|\s+inside$")
_NOT_NAME_CHARS = re.compile(r"[^\w\s&'’.\-]")
_TAIL_KEEP = {"of", "de", "del", "in", "at", "the", "and", "&", "y", "by", "on"}
_COMPLEX_RE = re.compile(
    r"^the shops at\b|\b(?:mall|plaza|shopping center|shopping centre|marketplace|outlets?|"
    r"packing district|food hall|town center|town centre)\b",
    re.IGNORECASE,
)


def _trim_venue(name: Optional[str]) -> Optional[str]:
    """Drop a location tail the caption tacked onto a name: 'Miopane in Pasadena',
    'Concerto • Koreatown', 'En Familia - Mexican Steakhouse', 'Protein Bao inside',
    'Miopane Pasadena' (a known city as the last word). A name that is only a tail
    is left alone."""
    if not name:
        return name
    s = _NAME_TAIL.sub("", name)
    s = _NAME_SPLIT.split(s, maxsplit=1)[0]
    s = _IN_PLACE_TAIL.sub("", s)
    words = s.split()
    for n in (3, 2, 1):                       # a known city/area as the last word(s)
        if len(words) > n and words[-n - 1].lower() not in _TAIL_KEEP:
            if " ".join(words[-n:]).lower() in _AREA_CITY_SET:
                words = words[:-n]
                break
    while len(words) > 1 and words[-1].lower() in _TAIL_KEEP:   # 'Wu and', 'Kobashi Ramen of'
        words = words[:-1]
    s = " ".join(words).strip(" ,.-–—")
    return s if len(s) >= 2 else name


def _pin_name(text: str) -> Optional[str]:
    """The venue named on a 📍 line or a bare name line, cleaned — or None when the
    line is an address, a city, a container or a sentence."""
    s = _PIN_LEAD.sub("", _PIN_LABEL.sub("", (text or "").strip()))
    s = re.sub(r"(?<![\w.])[@#][\w.]+|\S*\w\.\w\S*|https?://\S+", " ", s)  # @handles, #tags, domains
    if len(re.findall(r"\S*\d\S*", s)) >= 2:
        return None                           # several numbers: an address in any script
    head, sep, rest = s.partition(":")
    if sep and rest.strip() and len(head.split()) <= 3:
        s = rest                              # 'Mountain View: Matcha Mori' — place, then name
    s = _trim_venue(s.split(",")[0]) or ""
    s = " ".join(_NOT_NAME_CHARS.sub(" ", s).split()).strip(" .-'’")
    if len(s) < 3 or re.match(r"\d", s) or _ADDRESS_RE.search(s):
        return None
    toks = s.split()
    if len(toks) > 6 or (s.islower() and len(toks) >= 4):
        return None
    low = s.lower()
    if (_is_container(s) or _is_region(s) or low in _CHROME_LOCATIONS or low in _PIN_COUNTRY
            or _COMPLEX_RE.search(s)):
        return None
    return _titlecase_if_flat(s)


def _pinned_venue(caption: str, mentions: list[str]) -> tuple[Optional[str], str]:
    """A venue the caption pins down: a 📍 line naming a place ('📍 HANA Gelateria',
    '📍 Location: Mizuri Coffee', '📍NEW! BoBaPoP Tea Bar - San Marcos'), or the
    name line right above a 📍 address ('Melt Coffee ⏎ 📍17181 Redmond Wy').
    A one-word pin must match an @mention or end in a venue word; one-word place
    names are too often a city we don't know ('📍Sydney')."""
    lines = [ln.strip() for ln in caption.splitlines()]
    flat_mentions = {re.sub(r"[^a-z0-9]", "", m.lower()) for m in mentions}
    for i, line in enumerate(lines):
        if "📍" not in line:
            continue
        body = line.split("📍", 1)[1]
        name = _pin_name(body)
        if name:
            flat = re.sub(r"[^a-z0-9]", "", name.lower())
            if (len(name.split()) >= 2 or flat in flat_mentions
                    or _PIN_VENUE_KW.search(name)):
                return name, "pin"
            continue
        # an address (or a city) pin: the venue is the name line above it
        if not (_ADDRESS_RE.search(_PIN_LABEL.sub("", body.strip())) or _is_region(body.strip(" ,."))):
            continue
        for j in range(i - 1, max(-1, i - 3), -1):
            above = lines[j]
            if not above:
                continue
            if "📍" in above:
                if _is_region(above.split("📍", 1)[1].strip(" ,.")):
                    continue                  # 'Name ⏎ 📍City ⏎ 📍address'
                break
            if (re.search(r"[.!?:]\s*$", above) or len(above) > 48
                    or re.match(r"^\s*(?:[•*\-–→✅]|\d{1,2}[.)])", above)):
                break                         # a sentence or a list item, not a name line
            name = _pin_name(above)
            words = re.findall(r"[^\W\d_][\w'’]*", above)
            capped = sum(1 for w in words if w[:1].isupper())
            if name and (above.isupper() or above.islower() or capped * 2 >= len(words)):
                return name, "name_line"            # not 'Family owned & operated'
            break
    return None, "none"


_NAME_RUN = (r"(?P<venue>[A-Z0-9][\w'’&.\-]*"
             r"(?:\s+(?:(?:&|and|of|de|del|la|the|x)\s+)?[A-Z0-9][\w'’&.\-]*){0,5})")
# "<Name> is serving…", "<Name> just opened…", "<Name> was so good" at the start of
# a sentence — the way most reviews introduce the place.
_SUBJECT_VENUE = re.compile(
    r"(?:^|[.!?]\s+|\n\s*)" + _NAME_RUN +
    r"(?:\s+in\s+[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)?)?"
    r"\s+(?:is|was|has|had|just|came|serves|is serving|opened|offers|makes|brings|blew)\b"
)
# "check out <Name>", "one visit to <Name>", sentence-initial "At <Name>,",
# "at all <Name> branches".
_INTRO_VENUE = re.compile(
    r"\b(?:check out|visit to|head(?:ed)? to|went to)\s+" + _NAME_RUN +
    r"|(?:^|[.!?]\s+|\n\s*)At\s+" + _NAME_RUN.replace("?P<venue>", "?P<venue2>") + r"\s*,"
    r"|\bat (?:all|any|every)\s+" + _NAME_RUN.replace("?P<venue>", "?P<venue3>") +
    r"\s+(?:branches|locations|stores|shops)\b"
)
# Capitalized words that open sentences but never name a venue on their own.
_NOT_A_NAME = {
    "this", "that", "these", "those", "it", "its", "it's", "we", "i", "you", "they", "he",
    "she", "my", "our", "your", "their", "the", "a", "an", "pov", "today", "tonight",
    "yesterday", "everything", "everyone", "nothing", "something", "who", "what", "why",
    "how", "when", "where", "if", "and", "but", "so", "also", "plus", "not", "just", "best",
    "new", "here", "there", "one", "each", "every", "all", "some", "most", "she's", "he's",
    "we're", "they're", "you're", "i'm", "there's", "here's", "that's", "what's", "let's",
    "dinner", "lunch", "brunch", "breakfast", "dessert", "coffee", "food", "service",
    "everything", "price", "parking", "it’s", "that’s", "there’s", "here’s", "what’s",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "omg", "wow", "yes", "no",
    # foods and flavours that open sentences ("Mango is the best…")
    "mango", "strawberry", "strawberries", "banana", "apple", "peach", "cherry", "lemon",
    "lime", "orange", "grape", "melon", "watermelon", "pineapple", "coconut", "avocado",
    "vanilla", "caramel", "honey", "butter", "cheese", "ube", "taro", "pistachio",
    "hazelnut", "cinnamon", "garlic", "truffle", "salmon", "tuna", "egg", "eggs", "toast",
}


_FOOD_WORDS = {k for _, kws in _CATEGORY_KEYWORDS for k in kws}


def _plausible_name(name: Optional[str]) -> Optional[str]:
    """A captured Title-Case run that could be a venue: not a pronoun or sentence
    opener, not a city or a named complex, and not just a food word."""
    if not name:
        return None
    name = _trim_venue(name.strip(" .,!?:;-")) or ""
    toks = name.split()
    if not toks or len(name) < 3 or toks[0].lower() in _NOT_A_NAME:
        return None
    if _is_container(name) or _is_region(name) or name.lower() in _CHROME_LOCATIONS:
        return None
    if all(t.lower().strip("'’s") in _FOOD_WORDS or t.lower() in _NOT_A_NAME for t in toks):
        return None
    return name


def _sentence_venue(caption: str) -> Optional[str]:
    """A venue introduced in prose: '<Name> is serving…', 'check out <Name>', 'At <Name>,'."""
    for rx in (_INTRO_VENUE, _SUBJECT_VENUE):
        for m in rx.finditer(caption):
            raw = next((g for g in m.groups() if g), None)
            name = _plausible_name(raw)
            if name:
                return name
    return None


def _venue_slot(raw: RawPostSnapshot, caption: str) -> tuple[Optional[str], str]:
    """(venue, slot-name). Slot-first: structured > 'owner of @x' > a 📍 line naming
    the venue > venue-mention (from/by/@handle, quoted) > a name line matching the
    @mention > the single @mention > the name line above a 📍 address > first-person
    poster > prose ('Messina is serving…') > 'at <Venue>' > account title. Named
    complexes ('Disneyland') and cities never win on their own — they're context."""
    if _looks_like_list(raw, caption):
        return None, "list"
    if raw.venue_candidate:
        cleaned = _clean_venue_tag(raw.venue_candidate)
        if cleaned:
            return cleaned, "jsonld"

    dash_venue, _ = _name_dash_city(caption)  # "🍕 Folks Pizzeria — Culver City"
    if dash_venue:
        return dash_venue, "from_titlecase"

    m = _VENUE_COLON.match(caption)          # "Onn Cafe: $ 📍…" review-post style
    if m and not _is_container(m.group("venue")):
        return m.group("venue").strip(), "at_venue"

    m = _OWNER_OF.search(caption)
    if m:
        return _caption_spelling(_handle_to_name(m.group("handle")), caption), "handle_from"
    author = (getattr(raw, "author_handle", "") or "").lstrip("@").lower()
    mentions = [h for h in dict.fromkeys(_ANY_HANDLE.findall(caption)) if h.lower() != author]
    pinned, pin_slot = _pinned_venue(caption, mentions)
    if pinned and pin_slot == "pin":
        return pinned, pin_slot                 # an explicit 📍 name beats a "by @chef" credit
    m = _FROM_HANDLE.search(caption)
    if m:
        return _caption_spelling(_handle_to_name(m.group("handle")), caption), "handle_from"
    for m in _FROM_VENUE.finditer(caption):
        if _NEAR_LANDMARK.search(caption[max(0, m.start() - 30): m.start() + 5]):
            continue                           # "just steps from Climate Pledge Arena"
        cand = _trim_venue(m.group("venue").strip(" .,!?:;"))
        if cand and not _is_container(cand) and not _PERSON_LEAD.match(cand) and not _is_region(cand):
            return cand, "from_titlecase"

    m = _CALLED.search(caption)               # "... it's called Cento Pasta"
    if m and not _is_container(m.group("venue")) and not _is_region(m.group("venue")):
        return m.group("venue").strip(" .,!?:;"), "quoted"

    q = _QUOTED_NAME.search(caption)
    if q and not _is_container(q.group(1)):
        name = q.group(1).strip(" .,!?:;")
        bait = name.isupper() and len(name) <= 8
        if name and not bait and not _COMMENT_BAIT.search(caption[: q.start() + 12]):
            return name, "quoted"

    flat_mentions = {re.sub(r"[^a-z0-9]", "", m.lower()) for m in mentions}
    if pinned and re.sub(r"[^a-z0-9]", "", pinned.lower()) in flat_mentions:
        return pinned, pin_slot                 # a name line that is the @mention, spaced
    if len(mentions) == 1 and not _BLOGGER_HANDLE.search(mentions[0]):
        name = _caption_spelling(_handle_to_name(mentions[0]), caption)
        return _fuller_form(name, caption), "bare_mention"
    if pinned:
        return pinned, pin_slot                 # the name line above a 📍 address

    if (_FIRST_PERSON.search(caption) and not _BLOGGER_HANDLE.search(author)
            and (getattr(raw, "author_handle", None) or getattr(raw, "author_name", None))):
        name = _clean_display_name(getattr(raw, "author_name", None))
        if not name:
            name = _caption_spelling(_handle_to_name(raw.author_handle), caption)
        return name, "first_person"

    sentence = _sentence_venue(caption)        # "Messina is serving up…", "check out X"
    if sentence:
        return sentence, "sentence"

    pm = _PIN_LINE.search(caption)                # "📍 Grounded Coffee House"
    if pm:
        pin = _clean_pin(pm.group("loc")) or ""
        toks = pin.split()
        flat = re.sub(r"[^a-z0-9]", "", pin.lower())
        if (2 <= len(toks) <= 5 and not re.search(r"\d", pin)
                and not _is_container(pin) and not _is_region(pin)
                and (_PIN_VENUE_KW.search(pin)
                     or flat in {re.sub(r"[^a-z0-9]", "", m) for m in mentions})):
            return _titlecase_if_flat(pin.strip(" .,")), "at_venue"

    for at_match in _AT_VENUE.finditer(caption):
        cand = _trim_venue(at_match.group("venue").strip())
        if cand and not _is_container(cand) and not _is_region(cand) and not _COMPLEX_RE.search(cand):
            return cand, "at_venue"

    if raw.title:
        cleaned = _TITLE_NOISE.sub("", raw.title)
        cleaned = cleaned.split("•")[0].split("|")[0].strip()
        if cleaned:
            return cleaned, "title_fallback"
    disp = _clean_display_name(getattr(raw, "author_name", None))
    if disp:                                   # the account's own name, last resort
        return disp, "title_fallback"
    return None, "none"


def _venue_candidate(raw: RawPostSnapshot, caption: str) -> Optional[str]:
    return _venue_slot(raw, caption)[0]


def _core_theme(raw: RawPostSnapshot, caption: str) -> Optional[str]:
    for source in (caption, raw.description, raw.title):
        if not source:
            continue
        first_line = next((line.strip() for line in source.splitlines() if line.strip()), "")
        if first_line:
            return first_line[:280]
    return None


# Word-boundary match per category so "#longbeach" no longer trips "beach", etc.
# An optional plural ending is allowed ("tacos", "fritters", "potatoes").
def _kw_regex(kws: tuple[str, ...]) -> re.Pattern:
    alts = "|".join(re.escape(k) for k in sorted(kws, key=len, reverse=True))
    return re.compile(r"\b(?:" + alts + r")(?:e?s)?\b", re.IGNORECASE)


_CATEGORY_RE = [(cat, _kw_regex(kws)) for cat, kws in _CATEGORY_KEYWORDS]
_PLACE_TYPE_RE = [(cat, _kw_regex(kws)) for cat, kws in _PLACE_TYPES]
_CATEGORY_ORDER = {cat: i for i, (cat, _) in enumerate(_CATEGORY_KEYWORDS)}

# Hashtags are run together ("#disneyfood", "#bestsushiinguwahati"), so they are
# searched by substring — but only for keywords long or distinctive enough not to
# hide inside unrelated words ("deli" in "delicious", "poke" in "pokemon"). Music
# tags are left out: "#housemusic" on a clip is a song, not a live-music venue.
_SHORT_TAG_KWS = {"taco", "food", "eats", "cafe", "cake", "boba", "pizza", "ramen", "sushi",
                  "brew", "hike", "kbbq", "bbq", "pho"}
_TAG_UNSAFE = {"deli", "poke", "club", "bowl", "chef", "rice", "fish", "pie", "tart",
               "mole", "chai", "bao", "booth", "dining", "bread", "potato"}
_CATEGORY_TAG_KWS = [
    (cat, tuple(k.replace(" ", "").replace("'", "") for k in kws
                if k not in _TAG_UNSAFE
                and (len(k.replace(" ", "")) >= 5 or k in _SHORT_TAG_KWS)))
    for cat, kws in _CATEGORY_KEYWORDS
    if cat is not EventCategory.LIVE_MUSIC
]


def _votes(rx: re.Pattern, text: str) -> float:
    """Keyword hits, at most 3 per keyword; generic food words count a quarter."""
    seen: dict[str, int] = {}
    for m in rx.finditer(text):
        kw = m.group(0).lower()
        seen[kw] = seen.get(kw, 0) + 1
    return sum(
        min(n, 3) * (_GENERIC_WEIGHT if (kw in _GENERIC_FOOD or kw.rstrip("s") in _GENERIC_FOOD) else 1.0)
        for kw, n in seen.items()
    )


def _category_scores(text: str, hashtags: Optional[list[str]] = None) -> dict[EventCategory, float]:
    """Votes per category from keywords in the text, run-together hashtags and emoji."""
    text = _FOOD_BAR.sub(lambda m: m.group(1), (text or "").replace("’", "'"))
    scores: dict[EventCategory, float] = {}
    for cat, rx in _CATEGORY_RE:
        votes = _votes(rx, text) + sum(1 for e in _CATEGORY_EMOJI.get(cat, "") if e in text)
        if votes:
            scores[cat] = votes
    for tag in hashtags or []:
        key = re.sub(r"[^a-z0-9]", "", tag.lower())
        if not key:
            continue
        for cat, kws in _CATEGORY_TAG_KWS:
            hit = next((k for k in kws if k in key), None)
            if hit:
                scores[cat] = scores.get(cat, 0.0) + (_GENERIC_WEIGHT if hit in _GENERIC_FOOD else 1.0)
    return scores


def _place_types(text: str) -> dict[EventCategory, float]:
    clean = _FOOD_BAR.sub(lambda m: m.group(1), _URL.sub(" ", text or "").replace("’", "'"))
    return {cat: v for cat, rx in _PLACE_TYPE_RE if (v := _votes(rx, clean))}


def _categorize(text: str, hashtags: Optional[list[str]] = None) -> EventCategory:
    """The best-supported category, or OTHER when nothing in the post points anywhere.
    The kind of place the caption names most (café, bar, pop-up, restaurant) decides
    first, then dish words, hashtags and emoji. The venue's own name is deliberately
    not used: a category is what the reel is about, so a S'mores Pizookie at "BJ's
    Restaurants" is dessert and a full meal at "Cafe Landwer" is food. `hashtags`,
    when given, are searched by substring — leave them out of `text`."""
    types = _place_types(text)
    if types:
        best = max(types.values())
        tied = [c for c, v in types.items() if v == best]
        return min(tied, key=lambda c: _CATEGORY_ORDER[c])
    scores = _category_scores(_URL.sub(" ", text or ""), hashtags)
    if not scores:
        return EventCategory.OTHER
    # On a tie in dish words, food & drink wins: it is the most common kind of spot,
    # and a pasta-and-cheesecake reel is a restaurant more often than a dessert shop.
    return max(scores, key=lambda c: (scores[c], c is EventCategory.FOOD_DRINK, -_CATEGORY_ORDER[c]))


def _heuristic_times(searchable: str, start_date_raw: Optional[str]) -> list[str]:
    mentions = _TIME_MENTION.findall(searchable)
    if start_date_raw:
        mentions.append(start_date_raw)
    return _dedupe(mentions)


_AD_MARKERS = re.compile(
    r"®|™|\b(?:special[-\s]?edition|limited[-\s]?edition|new flavou?r|available now|"
    r"shop now|link in bio|use code|giveaway|sweepstakes|drinkware|merch(?:\s?drop)?|"
    r"sponsored|#ad|paid partnership)\b",
    re.IGNORECASE,
)
_MUSIC_GENRE_TAGS = {
    "newmusic", "housemusic", "technomusic", "edm", "electronicmusic", "hiphop",
    "rap", "rnb", "indiemusic", "rockmusic", "djlife", "producerlife", "speedgarage",
    "garage", "dnb", "dubstep", "trap", "afrobeats",
}
# Any hint the post is actually about food/a venue — gates the real-estate rule.
_FOOD_HINT = re.compile(
    r"\b(food|eat|eats|restaurant|cafe|coffee|bakery|menu|dish|brunch|lunch|dinner|"
    r"tacos?|pizza|ramen|sushi|matcha|boba|dessert|bar|drinks?|foodie|bite)\b",
    re.IGNORECASE,
)


def looks_vague(raw: RawPostSnapshot, venue, geo, category, candidate_times, slot="none") -> bool:
    """In-house stand-in for the LLM's is_vague: True when a post isn't clearly
    about a place/event, so product ads and music clips don't enter the catalog.
    Conservative — it only fires when EVERY place signal is absent."""
    caption = raw.caption or ""
    text = f"{caption} {' '.join(raw.hashtags or [])}"
    handle = (getattr(raw, "author_handle", "") or "").lstrip("@")

    if slot == "list":                          # a ranking / roundup — no single spot to save
        return True

    # "Not a place" signals strong enough to fire even if a (likely bogus) venue
    # was pulled: a cooking-instructions post, or a tourism-board account. Not when
    # the venue came from a high-confidence slot (a real venue that mentions a recipe).
    if not raw.venue_candidate and slot not in ("jsonld", "handle_from", "first_person"):
        recipe_hits = len(set(m.group(0).lower() for m in _RECIPE_RE.finditer(caption)))
        bullet_qty = len(re.findall(r"[○●•\-]\s*\d+\s*(?:g|ml|tbsp|tsp|cup|cucharad)", caption, re.I))
        if recipe_hits >= 2 or bullet_qty >= 3 or (
            re.search(r"\brecipe\b", caption, re.I)
            and re.search(r"\bingredient|receta|ingrediente\b", caption, re.I)
        ):
            return True
    if _TOURISM_HANDLE.match(handle):
        return True
    # Real-estate / mortgage marketing that borrows food-post styling.
    if re.search(r"(realestate|realtor|homeloans?|mortgage|loanofficer|lender|dueteam)$",
                 handle, re.IGNORECASE) and not _FOOD_HINT.search(text):
        return True
    if re.search(r"\b(manifesting|watch what i attract|pre-?approved|open house|"
                 r"now listed|for sale|before their lease renews?)\b", caption, re.IGNORECASE) \
            and not venue and category is EventCategory.OTHER:
        return True

    if venue or geo.place_names or candidate_times:
        return False
    # No venue and no location — a short caption that is pure hype, an
    # engagement-bait teaser ("Comment LIST"), or a new-special announcement is
    # not a spot, even when it names a food ("the best no bake desserts").
    if len(caption) < 140 and re.search(
        r"\b(is back|back!|now open|new today|new season|limited time|last chance|"
        r"don'?t miss|lock (?:it |them )?down|you hungry|who'?s hungry|what would you order|"
        r"comment (?:below|for|['\"“]?[a-z]+['\"”]?)|tag (?:a friend|someone)|save this|"
        r"link in bio|dm (?:me |us )?for|obviously\??$|new specials?)\b",
        caption, re.IGNORECASE,
    ):
        return True
    if category is not EventCategory.OTHER:
        return False
    if _AD_MARKERS.search(text):
        return True
    tags = {re.sub(r"[^a-z0-9]", "", t.lower()) for t in (raw.hashtags or [])}
    return len(tags & _MUSIC_GENRE_TAGS) >= 2


def _place_from_hashtags(hashtags: list[str]) -> Optional[str]:
    """First area-gazetteer hit across the post's hashtags, else None."""
    for tag in hashtags or []:
        key = re.sub(r"[^a-z0-9]", "", tag.lower())
        if key in _AREA_HASHTAGS:
            return _AREA_HASHTAGS[key]
        for suffix in _HASHTAG_SUFFIXES:
            if key.endswith(suffix) and key[: -len(suffix)] in _AREA_HASHTAGS:
                return _AREA_HASHTAGS[key[: -len(suffix)]]
        for gaz_key, place in _AREA_HASHTAGS.items():
            if len(gaz_key) >= 6 and gaz_key in key:
                return place
    return None


def _place_from_containers(caption: str) -> Optional[str]:
    """A named complex in the caption ('… at Disneyland') pins a city, without the
    decoy risk of scanning prose for arbitrary city names."""
    low = caption.lower()
    for name in _CONTAINERS:
        if name in low:
            city = _AREA_HASHTAGS.get(re.sub(r"[^a-z0-9]", "", name))
            if city:
                return city
    return None


# Nav/chrome words that leaked in as a "location tag" from a scraped page — never
# a real place. Belt-and-suspenders behind the text_isolation.py href check.
_CHROME_LOCATIONS = {
    "locations", "location", "explore", "log in", "login", "sign up", "signup",
    "about", "home", "meta", "help", "privacy", "terms",
}


_PIN_COUNTRY = {"japan", "korea", "south korea", "china", "thailand", "vietnam", "mexico",
                "italy", "france", "spain", "taiwan", "india", "brazil", "hell's kitchen"}


def _clean_pin(loc: str) -> Optional[str]:
    """A 📍 line is often 'NEW! Name' or a whole sentence — keep the place part.
    An address is left intact (geo splits it on commas for the city)."""
    s = _PIN_LEAD.sub("", (loc or "").strip())
    if not s:
        return None
    if _ADDRESS_RE.search(s):
        return s
    parts = _PIN_SENTENCE.split(s, maxsplit=1)
    if len(parts) > 1 and len(parts[1].split()) >= 3:
        s = parts[0].strip()
    return " ".join(s.split()[:8]).strip(" .,·-") or None


def _in_city(caption: str) -> Optional[str]:
    """First 'in <City>' that isn't a country/macro-region; a gazetteer city wins."""
    _drop = _PIN_COUNTRY | {"socal", "southern california", "bay area", "the bay", "east bay"}
    cands = [m.group("city").strip() for m in _IN_CITY.finditer(caption)]
    cands = [c for c in cands if c.lower() not in _drop]
    if not cands:
        return None
    for c in cands:
        if c.lower() in _AREA_CITY_SET:
            return c
    return cands[0]


def _geo_context(raw: RawPostSnapshot, caption: str) -> GeoContext:
    raw_location_text = raw.location_text
    if raw_location_text and raw_location_text.strip().lower() in _CHROME_LOCATIONS:
        raw_location_text = None
    source, confidence = GeoSource.NONE, 0.0
    hashtag_place = _place_from_hashtags(raw.hashtags) or _place_from_containers(caption)

    _, dash_city = _name_dash_city(caption)

    if raw_location_text or (raw.lat is not None and raw.lng is not None):
        source, confidence = GeoSource.PLATFORM_LOCATION_TAG, 0.9
    else:
        pin_match = _PIN_LINE.search(caption)
        pin_loc = _clean_pin(pin_match.group("loc")) if pin_match else None
        if pin_loc:
            raw_location_text = pin_loc
            source, confidence = GeoSource.CAPTION_TEXT, 0.6
        elif _in_city(caption):
            source, confidence = GeoSource.CAPTION_TEXT, 0.5
        elif dash_city:
            source, confidence = GeoSource.CAPTION_TEXT, 0.55
        elif hashtag_place:
            source, confidence = GeoSource.HASHTAG, 0.4
        elif raw.hashtags:
            source, confidence = GeoSource.HASHTAG, 0.2

    place_names: list[str] = []
    if raw_location_text:
        place_names = [part.strip() for part in raw_location_text.split(",") if part.strip()]
    city = _in_city(caption)
    if city and city not in place_names:
        place_names.append(city)
    if dash_city and dash_city not in place_names:
        place_names.append(dash_city)
    if hashtag_place and hashtag_place not in place_names:
        place_names.append(hashtag_place)

    return GeoContext(
        raw_location_text=raw_location_text,
        place_names=place_names,
        lat=raw.lat,
        lng=raw.lng,
        source=source,
        confidence=confidence,
    )


# ── LLM merge helpers ────────────────────────────────────────────────────────


def _merge_geo(geo: GeoContext, ext: LlmExtraction) -> GeoContext:
    """Layer the LLM's location reading over the heuristic geo.

    Coordinates and an explicit platform location tag are code-owned and outrank
    the model; we only enrich their place_names. Otherwise the LLM may supply a
    location the regexes missed.
    """
    if geo.source is GeoSource.PLATFORM_LOCATION_TAG:
        return geo.model_copy(
            update={"place_names": _dedupe([*geo.place_names, *ext.place_names])}
        )
    if ext.raw_location_text:
        places = _dedupe(
            [*_split_location(ext.raw_location_text), *ext.place_names, *geo.place_names]
        )
        return GeoContext(
            raw_location_text=ext.raw_location_text,
            place_names=places,
            lat=geo.lat,
            lng=geo.lng,
            source=GeoSource.CAPTION_TEXT,
            confidence=0.6,
        )
    if ext.place_names:
        return geo.model_copy(
            update={
                "place_names": _dedupe([*geo.place_names, *ext.place_names]),
                "source": geo.source if geo.source is not GeoSource.NONE else GeoSource.CAPTION_TEXT,
                "confidence": max(geo.confidence, 0.5),
            }
        )
    return geo


# ── small utilities ──────────────────────────────────────────────────────────


def _clean(text: Optional[str]) -> str:
    return _URL.sub("", text or "").strip()


def _split_location(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving, case-insensitive dedupe."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out
