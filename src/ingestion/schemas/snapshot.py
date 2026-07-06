"""Lenient intake models: capture scrape reality first, judge it later.

Strictness deliberately lives in inspiration.py — these models accept whatever
the page actually served so that nothing is lost before validation.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from src.ingestion.schemas.results import FetchStatus


class TextRole(str, Enum):
    CAPTION = "caption"
    TITLE = "title"
    LOCATION_TAG = "location_tag"
    COMMENT = "comment"
    OTHER = "other"


class TextComponent(BaseModel):
    role: TextRole = TextRole.OTHER
    text: str
    selector: Optional[str] = None


class PageSnapshot(BaseModel):
    """What one navigation actually produced: HTML, metadata, isolated text."""

    model_config = ConfigDict(extra="allow")

    url: str
    final_url: Optional[str] = None
    status: FetchStatus
    fetched_at: datetime
    html: Optional[str] = None
    meta: dict[str, str] = {}     # name/property -> content for all <meta> tags
    jsonld: list[Any] = []        # parsed application/ld+json blocks
    components: list[TextComponent] = []

    def first_text(self, role: TextRole) -> Optional[str]:
        for component in self.components:
            if component.role is role:
                return component.text
        return None


class RawPostSnapshot(BaseModel):
    """Extractor output: best-effort fields, everything optional, extras kept."""

    model_config = ConfigDict(extra="allow")

    source_url: str
    platform: str = "generic"
    extractor: str = "unknown"
    fetched_at: Optional[datetime] = None
    caption: Optional[str] = None
    transcript: Optional[str] = None       # speech-to-text of the reel's audio (Phase 2.5)
    video_url: Optional[str] = None        # reel mp4 for the transcriber (set by fetch/extract)
    title: Optional[str] = None
    description: Optional[str] = None
    author_handle: Optional[str] = None
    image_url: Optional[str] = None        # og:image — thumbnail for spot cards
    location_text: Optional[str] = None
    hashtags: list[str] = []
    mentions: list[str] = []
    lat: Optional[float] = None
    lng: Optional[float] = None
    start_date_raw: Optional[str] = None   # e.g. JSON-LD startDate, kept verbatim
    venue_candidate: Optional[str] = None  # e.g. JSON-LD Place name
