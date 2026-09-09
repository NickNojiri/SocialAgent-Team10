"""Field candidates from a raw snapshot: heuristic baseline, optionally refined by the LLM.

Two pure entry points (no I/O, no network — the LLM call lives in llm_extractor):
  - build_llm_payload(raw): clean, capped JSON payload for the model.
  - normalize(raw, llm_extraction=None): candidate kwargs for EventInspiration.

The heuristic baseline is always computed; when an LlmExtraction is supplied its
grounded fields override the heuristics, while coordinates, hashtags, hashes, and
provenance stay code-owned (merged, never delegated).
"""

import re
from typing import Optional

from src.ingestion.schemas.extraction import LlmExtraction
from src.ingestion.schemas.inspiration import EventCategory, GeoContext, GeoSource
from src.ingestion.schemas.snapshot import RawPostSnapshot

# Checked in order — more specific categories first, FOOD_DRINK as the broad net.
_CATEGORY_KEYWORDS: list[tuple[EventCategory, tuple[str, ...]]] = [
    (EventCategory.CAFE_DESSERT, (
        "cafe", "café", "coffee", "espresso", "latte", "dessert", "boba", "bubble tea",
        "bakery", "pastry", "croissant", "ice cream", "gelato", "matcha", "cake",
        "donut", "doughnut", "churro", "crepe", "s'more", "smore", "fresas con crema",
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
        "taco", "birria", "restaurant", "food", "brunch", "breakfast", "lunch", "dinner",
        "eats", "eatery", "kitchen", "grill", "diner", "bistro", "ramen", "sushi",
        "omakase", "poke", "bbq", "barbecue", "korean bbq", "kbbq", "pizza", "burger",
        "burrito", "quesadilla", "nachos", "carne asada", "al pastor", "pho", "noodle",
        "dumpling", "dim sum", "hot pot", "naan", "curry", "pastrami", "sandwich",
        "deli", "wings", "fried chicken", "crispy pata", "steak", "seafood", "oyster",
        "lobster", "crab", "dessert menu", "menu", "erewhon",
    )),
]

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
    searchable = " ".join(
        filter(None, [raw.caption, raw.transcript, raw.frame_text, raw.title, raw.description,
                      " ".join(raw.hashtags or [])])
    )

    # 1. Heuristic baseline — always computed, deterministic, cheap.
    venue, venue_slot = _venue_slot(raw, caption)
    if venue is None and raw.transcript:
        # spoken-only venue: run the mention/from/at/called slots over the transcript
        t_venue, _ = _venue_slot(raw, raw.transcript)
        if t_venue:
            venue, venue_slot = t_venue, "transcript"
    geo = _geo_context(raw, caption if not raw.transcript else f"{caption}\n{raw.transcript}")
    if venue is None and geo.raw_location_text:
        guess = geo.raw_location_text.split(",")[0].strip() or None
        if guess and not _ADDRESS_RE.search(guess) and not _is_container(guess):
            venue, venue_slot = guess, "at_venue"  # weak: a bare location string
    candidate_times = _heuristic_times(searchable, raw.start_date_raw)
    category = _categorize(searchable.lower())
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
        "is_vague": looks_vague(raw, venue, geo, category, candidate_times, venue_slot),
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


# Which slot filled the venue → a confidence the rest of the system can act on
# (route low-confidence to human review; spend the LLM only on the uncertain tail).
VENUE_CONF = {
    "jsonld": 0.95, "handle_from": 0.85, "first_person": 0.8, "quoted": 0.7,
    "from_titlecase": 0.7, "bare_mention": 0.6, "at_venue": 0.55, "transcript": 0.5,
    "title_fallback": 0.3, "llm_fill": 0.35, "none": 0.0,
}


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


def _venue_slot(raw: RawPostSnapshot, caption: str) -> tuple[Optional[str], str]:
    """(venue, slot-name). Slot-first: structured > venue-mention (from/by/@handle)
    > first-person poster > 'at <Venue>' > account title. Named complexes
    ('Disneyland') never win on their own — they're context, not the venue."""
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

    for pattern in (_OWNER_OF, _FROM_HANDLE):
        m = pattern.search(caption)
        if m:
            return _caption_spelling(_handle_to_name(m.group("handle")), caption), "handle_from"
    m = _FROM_VENUE.search(caption)
    if m:
        cand = m.group("venue").strip(" .,!?:;")
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

    author = (getattr(raw, "author_handle", "") or "").lstrip("@").lower()
    mentions = [h for h in dict.fromkeys(_ANY_HANDLE.findall(caption)) if h.lower() != author]
    if len(mentions) == 1 and not _BLOGGER_HANDLE.search(mentions[0]):
        return _caption_spelling(_handle_to_name(mentions[0]), caption), "bare_mention"

    if _FIRST_PERSON.search(caption) and getattr(raw, "author_handle", None):
        return _caption_spelling(_handle_to_name(raw.author_handle), caption), "first_person"

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

    at_match = _AT_VENUE.search(caption)
    if at_match and not _is_container(at_match.group("venue")) and not _is_region(at_match.group("venue")):
        return at_match.group("venue").strip(), "at_venue"

    if raw.title:
        cleaned = _TITLE_NOISE.sub("", raw.title)
        cleaned = cleaned.split("•")[0].split("|")[0].strip()
        if cleaned:
            return cleaned, "title_fallback"
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
_CATEGORY_RE = [
    (cat, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b", re.IGNORECASE))
    for cat, kws in _CATEGORY_KEYWORDS
]


def _categorize(lowered_text: str) -> EventCategory:
    for category, pattern in _CATEGORY_RE:
        if pattern.search(lowered_text):
            return category
    return EventCategory.OTHER


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

    if venue or geo.place_names or candidate_times or category is not EventCategory.OTHER:
        return False
    if _AD_MARKERS.search(text):
        return True
    # Pure hype from a place's own account with no location anywhere.
    if len(caption) < 120 and re.search(
        r"\b(is back|back!|now open|limited time|last chance|don'?t miss|lock (?:it |them )?down)\b",
        caption, re.IGNORECASE,
    ):
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
