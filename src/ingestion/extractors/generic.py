"""schema.org JSON-LD / OpenGraph fallback — blogs, event pages, link-in-bio targets.

This is the most reliable adapter because the data is published intentionally:
Event/Place/Restaurant markup carries venue names, addresses, coordinates, and
start dates verbatim.
"""

from typing import Any, Iterator, Optional

from src.ingestion.extractors.base import HASHTAG_RE
from src.ingestion.schemas.snapshot import PageSnapshot, RawPostSnapshot, TextRole

_EVENTISH = {"Event", "FoodEvent", "SocialEvent", "MusicEvent", "Festival"}
_PLACEISH = {
    "Place",
    "Restaurant",
    "LocalBusiness",
    "BarOrPub",
    "CafeOrCoffeeShop",
    "FoodEstablishment",
}


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GenericExtractor:
    name = "generic"
    version = "0.1"

    def claims(self, url: str) -> bool:
        return True  # always the fallback

    def extract(self, snapshot: PageSnapshot) -> RawPostSnapshot:
        title = snapshot.meta.get("og:title") or snapshot.first_text(TextRole.TITLE)
        description = snapshot.meta.get("og:description") or snapshot.meta.get("description")
        venue: Optional[str] = None
        location_text: Optional[str] = None
        lat: Optional[float] = None
        lng: Optional[float] = None
        start_date: Optional[str] = None

        for node in self._nodes(snapshot.jsonld):
            node_type = node.get("@type")
            types = set(node_type) if isinstance(node_type, list) else {node_type}

            if types & _EVENTISH:
                title = node.get("name") or title
                description = node.get("description") or description
                start_date = start_date or node.get("startDate")
                location = node.get("location")
                if isinstance(location, dict):
                    venue = venue or location.get("name")
                    location_text = location_text or self._address_text(location.get("address"))
                    lat, lng = self._coords(location.get("geo"), lat, lng)
            elif types & _PLACEISH:
                venue = venue or node.get("name")
                location_text = location_text or self._address_text(node.get("address"))
                lat, lng = self._coords(node.get("geo"), lat, lng)

        text_pool = " ".join(filter(None, [title, description]))

        return RawPostSnapshot(
            source_url=snapshot.url,
            platform="generic",
            extractor=f"{self.name}/{self.version}",
            fetched_at=snapshot.fetched_at,
            caption=description,
            title=title,
            description=description,
            location_text=location_text,
            hashtags=HASHTAG_RE.findall(text_pool),
            lat=lat,
            lng=lng,
            start_date_raw=start_date,
            venue_candidate=venue,
        )

    @staticmethod
    def _nodes(jsonld: list) -> Iterator[dict]:
        """Yield JSON-LD nodes, flattening @graph containers."""
        for block in jsonld:
            if not isinstance(block, dict):
                continue
            graph = block.get("@graph")
            if isinstance(graph, list):
                yield from (node for node in graph if isinstance(node, dict))
            else:
                yield block

    @staticmethod
    def _address_text(address: Any) -> Optional[str]:
        if isinstance(address, str):
            return address
        if isinstance(address, dict):
            parts = [
                address.get(key)
                for key in ("streetAddress", "addressLocality", "addressRegion")
            ]
            joined = ", ".join(part for part in parts if part)
            return joined or None
        return None

    @staticmethod
    def _coords(
        geo: Any, lat: Optional[float], lng: Optional[float]
    ) -> tuple[Optional[float], Optional[float]]:
        if isinstance(geo, dict):
            if lat is None:
                lat = _as_float(geo.get("latitude"))
            if lng is None:
                lng = _as_float(geo.get("longitude"))
        return lat, lng
