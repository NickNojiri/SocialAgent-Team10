"""Resolve GeoContext location clues into coordinates (Phase 3).

Wraps the repo's existing `src/services/location_service.py::address_to_coords`
(Nominatim) without modifying it. The geocoder is injectable so the whole class
is unit-testable offline; the default is the real network function, imported
lazily so the --no-geocode path never pulls in `requests`.

Design mirrors the Phase 2 LLM layer: best-effort, default-on, and a geocoder
failure is a normal "unresolved" outcome — the pipeline never fails because
geocoding did.
"""

import logging
import re
from typing import Callable, Optional

from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.inspiration import EventInspiration, GeoContext, GeoResolution

log = logging.getLogger("ingestion.geo")

# A geocoder maps a free-text query to (lat, lng) or (None, None) on no match.
Geocoder = Callable[[str], tuple[Optional[float], Optional[float]]]

# Confidence by how the coordinate was obtained (see plan's evaluator table).
CONF_EXPLICIT = 0.9        # floor for page-supplied coords
CONF_GEOCODED = 0.75       # a specific clue (address / venue+city) resolved
CONF_GEOCODED_BROAD = 0.45  # only a city-level fallback resolved
UNRESOLVED_DECAY = 0.5     # multiply prior confidence when a present clue won't resolve

_WS = re.compile(r"\s+")


def _default_geocoder() -> Geocoder:
    # Lazy import: only touch requests/the shared service when actually geocoding.
    from src.services.location_service import address_to_coords

    return address_to_coords


class GeoEnricher:
    def __init__(self, settings: IngestionSettings, geocoder: Optional[Geocoder] = None):
        self.settings = settings
        self._geocoder = geocoder or _default_geocoder()
        # In-run cache: a city repeated across many posts is fetched once. Also
        # makes per-record query-tier retries free when they overlap.
        self._cache: dict[str, tuple[Optional[float], Optional[float]]] = {}

    def enrich(self, record: EventInspiration) -> EventInspiration:
        """Return a copy of the record with its GeoContext resolved/scored."""
        new_geo = self._resolve(record.geo, record.venue_name)
        return record.model_copy(update={"geo": new_geo})

    # ── internals ────────────────────────────────────────────────────────────

    def _resolve(self, geo: GeoContext, venue_name: str) -> GeoContext:
        # 1. Already explicit: trust page coords, don't call Nominatim.
        if geo.lat is not None and geo.lng is not None:
            return geo.model_copy(
                update={
                    "resolution": GeoResolution.EXPLICIT,
                    "confidence": max(geo.confidence, CONF_EXPLICIT),
                }
            )

        specific, broad = self._candidate_queries(geo, venue_name)
        if not specific and not broad:
            # 2. Nothing to geocode — leave the (already low) confidence as-is.
            return geo.model_copy(update={"resolution": GeoResolution.UNRESOLVED})

        # 3. Specific tiers first (capped), then the broad city fallback.
        for query in specific[: self.settings.max_geocode_queries]:
            lat, lng = self._lookup(query)
            if lat is not None and lng is not None:
                return self._hit(geo, lat, lng, GeoResolution.GEOCODED, CONF_GEOCODED, query)

        for query in broad:
            lat, lng = self._lookup(query)
            if lat is not None and lng is not None:
                return self._hit(geo, lat, lng, GeoResolution.GEOCODED_BROAD, CONF_GEOCODED_BROAD, query)

        # 4. Had clues but none resolved — down-weight, keep clue, coords stay None.
        log.info(f"[geo] unresolved: {specific + broad}")
        return geo.model_copy(
            update={
                "resolution": GeoResolution.UNRESOLVED,
                "confidence": round(geo.confidence * UNRESOLVED_DECAY, 2),
            }
        )

    def _candidate_queries(
        self, geo: GeoContext, venue_name: str
    ) -> tuple[list[str], list[str]]:
        """Build (specific, broad) query lists, deduped and order-preserving."""
        city = geo.place_names[-1] if geo.place_names else None

        specific: list[str] = []
        if geo.raw_location_text:
            specific.append(geo.raw_location_text)
        if venue_name and city and venue_name.lower() != city.lower():
            specific.append(f"{venue_name}, {city}")
        if geo.place_names:
            specific.append(", ".join(geo.place_names))

        broad: list[str] = [city] if city else []

        # A city-only specific query is really a broad match — don't double-count
        # it as "specific". (Happens when the sole clue is a city name.)
        specific = [q for q in specific if not (city and q.strip().lower() == city.lower())]

        return _dedupe(specific), _dedupe(broad)

    def _hit(
        self,
        geo: GeoContext,
        lat: float,
        lng: float,
        resolution: GeoResolution,
        confidence: float,
        query: str,
    ) -> GeoContext:
        log.info(f"[geo] {resolution.value} via {query!r} -> ({lat:.4f}, {lng:.4f})")
        return geo.model_copy(
            update={"lat": lat, "lng": lng, "resolution": resolution, "confidence": confidence}
        )

    def _lookup(self, query: str) -> tuple[Optional[float], Optional[float]]:
        key = _norm(query)
        if key in self._cache:
            return self._cache[key]
        try:
            result = self._geocoder(query)
        except Exception as exc:  # belt-and-suspenders; address_to_coords already guards
            log.warning(f"[geo] geocoder error for {query!r} ({exc}); treating as no match")
            result = (None, None)
        self._cache[key] = result
        return result


def _norm(text: str) -> str:
    return _WS.sub(" ", text.strip().lower())


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = _norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out
