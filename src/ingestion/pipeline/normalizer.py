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
    (EventCategory.CAFE_DESSERT, ("cafe", "coffee", "dessert", "boba", "bakery", "ice cream", "matcha")),
    (EventCategory.LIVE_MUSIC, ("live music", "concert", "band", "dj set", "jazz", "open mic", "vinyl night")),
    (EventCategory.MARKET_POPUP, ("pop-up", "popup", "night market", "food fair", "farmers market", "festival")),
    (EventCategory.NIGHTLIFE, ("bar", "cocktail", "brewery", "club", "happy hour", "speakeasy")),
    (EventCategory.OUTDOORS, ("hike", "beach", "park", "picnic", "trail", "kayak")),
    (EventCategory.COMMUNITY, ("meetup", "community", "volunteer", "workshop", "book club")),
    (EventCategory.FOOD_DRINK, ("taco", "birria", "restaurant", "food", "brunch", "dinner", "eats", "ramen", "sushi", "bbq", "pizza", "burger")),
]

_PIN_LINE = re.compile(r"📍\s*(?P<loc>[^\n#@—!]+)")
_AT_VENUE = re.compile(r"\bat\s+(?P<venue>[A-Z][\w'&-]*(?:\s+[A-Z][\w'&-]*){0,4})")
_IN_CITY = re.compile(r"\bin\s+(?P<city>[A-Z][a-zA-Z'-]*(?:\s+[A-Z][a-zA-Z'-]*){0,2})")
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
    searchable = " ".join(filter(None, [raw.caption, raw.title, raw.description]))

    # 1. Heuristic baseline — always computed, deterministic, cheap.
    venue = _venue_candidate(raw, caption)
    geo = _geo_context(raw, caption)
    if venue is None and geo.raw_location_text:
        venue = geo.raw_location_text.split(",")[0].strip() or None
    candidate_times = _heuristic_times(searchable, raw.start_date_raw)
    category = _categorize(searchable.lower())
    core_theme = _core_theme(raw, caption)

    # 2. LLM layer — refines chaotic text; it never overrides structured data.
    if llm_extraction is not None:
        ext = llm_extraction
        # A JSON-LD Place name (raw.venue_candidate) is authoritative structured
        # data, like coordinates — the model doesn't get to second-guess it.
        if raw.venue_candidate:
            venue = raw.venue_candidate.strip()
        else:
            venue = ext.venue_name or venue
        core_theme = ext.core_theme or core_theme
        # Prefer a confident (non-OTHER) category. The model wins genuine ties on
        # messy captions; the keyword heuristic is the floor when the model is
        # unsure, so a clear "jazz concert" never regresses to "other".
        llm_category = _coerce_category(ext.category)
        if llm_category is not EventCategory.OTHER:
            category = llm_category
        candidate_times = _dedupe([*ext.candidate_times, *candidate_times])
        geo = _merge_geo(geo, ext)

    if venue:
        venue = venue[:110].strip()

    return {
        "venue_name": venue,
        "core_theme": core_theme,
        "category": category,
        "geo": geo,
        "hashtags": raw.hashtags,
        "candidate_times": candidate_times,
    }


# ── heuristic helpers (Phase 1, unchanged behavior) ─────────────────────────


def _venue_candidate(raw: RawPostSnapshot, caption: str) -> Optional[str]:
    if raw.venue_candidate:
        return raw.venue_candidate.strip()
    at_match = _AT_VENUE.search(caption)
    if at_match:
        return at_match.group("venue").strip()
    if raw.title:
        # Last resort: the page/account title. For a venue's own account this
        # is often correct ("Casa Loma Tacos (@casaloma) • Instagram…").
        cleaned = _TITLE_NOISE.sub("", raw.title)
        cleaned = cleaned.split("•")[0].split("|")[0].strip()
        return cleaned or None
    return None


def _core_theme(raw: RawPostSnapshot, caption: str) -> Optional[str]:
    for source in (caption, raw.description, raw.title):
        if not source:
            continue
        first_line = next((line.strip() for line in source.splitlines() if line.strip()), "")
        if first_line:
            return first_line[:280]
    return None


def _categorize(lowered_text: str) -> EventCategory:
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in lowered_text for keyword in keywords):
            return category
    return EventCategory.OTHER


def _heuristic_times(searchable: str, start_date_raw: Optional[str]) -> list[str]:
    mentions = _TIME_MENTION.findall(searchable)
    if start_date_raw:
        mentions.append(start_date_raw)
    return _dedupe(mentions)


def _geo_context(raw: RawPostSnapshot, caption: str) -> GeoContext:
    raw_location_text = raw.location_text
    source, confidence = GeoSource.NONE, 0.0

    if raw.location_text or (raw.lat is not None and raw.lng is not None):
        source, confidence = GeoSource.PLATFORM_LOCATION_TAG, 0.9
    else:
        pin_match = _PIN_LINE.search(caption)
        if pin_match:
            raw_location_text = pin_match.group("loc").strip()
            source, confidence = GeoSource.CAPTION_TEXT, 0.6
        elif _IN_CITY.search(caption):
            source, confidence = GeoSource.CAPTION_TEXT, 0.5
        elif raw.hashtags:
            source, confidence = GeoSource.HASHTAG, 0.2

    place_names: list[str] = []
    if raw_location_text:
        place_names = [part.strip() for part in raw_location_text.split(",") if part.strip()]
    city_match = _IN_CITY.search(caption)
    if city_match:
        city = city_match.group("city").strip()
        if city and city not in place_names:
            place_names.append(city)

    return GeoContext(
        raw_location_text=raw_location_text,
        place_names=place_names,
        lat=raw.lat,
        lng=raw.lng,
        source=source,
        confidence=confidence,
    )


# ── LLM merge helpers ────────────────────────────────────────────────────────


def _coerce_category(value: str) -> EventCategory:
    try:
        return EventCategory(value)
    except ValueError:
        return EventCategory.OTHER


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
