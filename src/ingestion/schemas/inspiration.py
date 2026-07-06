"""Strict, validated "Event Inspiration" records — the engine's only sanctioned output.

Everything upstream of pipeline/validator.py is untrusted scrape data; nothing
reaches a sink unless it passes these models.
"""

import hashlib
from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EventCategory(str, Enum):
    FOOD_DRINK = "food_drink"
    CAFE_DESSERT = "cafe_dessert"
    NIGHTLIFE = "nightlife"
    LIVE_MUSIC = "live_music"
    MARKET_POPUP = "market_popup"
    OUTDOORS = "outdoors"
    COMMUNITY = "community"
    OTHER = "other"


class GeoSource(str, Enum):
    PLATFORM_LOCATION_TAG = "platform_location_tag"
    CAPTION_TEXT = "caption_text"
    HASHTAG = "hashtag"
    NONE = "none"


class GeoResolution(str, Enum):
    """How the coordinate was obtained (Phase 3) — orthogonal to GeoSource, which
    records where the location *clue* came from."""

    EXPLICIT = "explicit"              # coords came from the page (JSON-LD / platform tag)
    GEOCODED = "geocoded"              # a specific clue (address / venue+city) resolved
    GEOCODED_BROAD = "geocoded_broad"  # only a broad fallback (city alone) resolved
    UNRESOLVED = "unresolved"          # nothing mappable, or geocoder returned nothing


class GeoContext(BaseModel):
    model_config = _STRICT

    raw_location_text: Optional[str] = None  # e.g. "4th St, Long Beach" from a 📍 line
    place_names: list[str] = []              # candidate venue/neighborhood/city strings
    lat: Optional[float] = Field(None, ge=-90, le=90)   # only if the page exposed coordinates
    lng: Optional[float] = Field(None, ge=-180, le=180)
    source: GeoSource = GeoSource.NONE
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    resolution: GeoResolution = GeoResolution.UNRESOLVED  # set by Phase 3 geo enrichment


class ScheduleStatus(str, Enum):
    SCHEDULED = "scheduled"      # active, future, start+end resolved
    UNSCHEDULED = "unscheduled"  # no parseable time (kept — still a venue/vibe inspiration)
    # 'expired' is never stored; historical events are rejected upstream (Phase 4).


class EventSchedule(BaseModel):
    model_config = _STRICT

    status: ScheduleStatus
    start_utc: Optional[datetime] = None   # tz-aware UTC
    end_utc: Optional[datetime] = None
    time_known: bool = False               # False when only a date was found (hour defaulted)
    parsed_from: Optional[str] = None      # the candidate string(s) that resolved


class SourceProvenance(BaseModel):
    model_config = _STRICT

    # AnyUrl (not HttpUrl) so offline file:// fixtures can flow the full
    # pipeline; SocialSessionManager enforces public http(s) for real runs.
    source_url: AnyUrl
    platform: Literal["instagram", "tiktok", "generic"]
    fetched_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)  # sha256 hex — dedupe key
    extractor: str                                           # adapter name/version
    temporal_parser: Optional[str] = None                    # e.g. "timeparser/dateparser-1.4.0"


# Venue strings that mean page chrome leaked through, not a real venue.
_BOILERPLATE_VENUES = {
    "instagram",
    "instagram.com",
    "log in",
    "login",
    "facebook",
    "page not found",
}


class EventInspiration(BaseModel):
    model_config = _STRICT

    record_id: UUID = Field(default_factory=uuid4)
    venue_name: str = Field(min_length=1, max_length=120)
    core_theme: str = Field(min_length=3, max_length=280)  # "late-night birria pop-up"
    # User-facing "quick description" from the reel's audio (Phase 2.5). None when
    # there was nothing to transcribe — the card shows "No info" in that case.
    summary: Optional[str] = Field(None, max_length=600)
    # Post thumbnail (og:image) for spot cards; code-owned display data, never
    # touched by the LLM. Non-http values are dropped in normalize().
    image_url: Optional[str] = Field(None, max_length=2000)
    category: EventCategory
    geo: GeoContext
    hashtags: list[str] = []
    # Raw time mentions; resolved into `schedule` by Phase 4 (TimeParser bridge).
    candidate_times: list[str] = []
    schedule: Optional[EventSchedule] = None  # None = not temporally processed yet
    provenance: SourceProvenance

    @field_validator("venue_name")
    @classmethod
    def venue_not_boilerplate(cls, value: str) -> str:
        if value.strip().lower() in _BOILERPLATE_VENUES:
            raise ValueError(f"venue_name {value!r} is platform boilerplate, not a venue")
        return value

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for tag in value:
            cleaned = tag.lstrip("#").strip().lower()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
