"""SocialAgent Phase 1 — local ingestion engine for public social pages.

Explicit public URLs in, strictly validated EventInspiration records out.
See src/ingestion/cli.py for the entry point.
"""

from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.inspiration import (
    EventCategory,
    EventInspiration,
    GeoContext,
    GeoSource,
    SourceProvenance,
)
from src.ingestion.schemas.results import FetchStatus, IngestionResult

__all__ = [
    "IngestionSettings",
    "EventCategory",
    "EventInspiration",
    "GeoContext",
    "GeoSource",
    "SourceProvenance",
    "FetchStatus",
    "IngestionResult",
]
