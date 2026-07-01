"""Authenticated Instagram source — the 95–99% reliability fetch path.

Replaces only the *fetch* stage of the pipeline (docs/IG_AUTH_INGESTION_PLAN.md):
an instagrapi session pulls caption, author, location (name + coordinates),
and media URLs by shortcode, and adapts them into the same RawPostSnapshot the
Playwright path produces — everything downstream (LLM → geo → temporal →
Chroma) is unchanged.

Guardrails, matching the rest of the pipeline:
- burner account only; credentials come from env, are never logged, and the
  session cookie is persisted once (data/ig_session.json, gitignored) so login
  happens a single time, not per run;
- instagrapi is imported lazily — importing this module never requires it, and
  tests inject a fake client (mirroring LlmFieldExtractor/GeoEnricher);
- any fetch/login error degrades to None, never a crash.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from src.ingestion.config import IngestionSettings
from src.ingestion.extractors.base import HASHTAG_RE, MENTION_RE
from src.ingestion.schemas.snapshot import RawPostSnapshot

log = logging.getLogger("ingestion.ig_authed")

_SHORTCODE_PATH = re.compile(r"^/(?:p|reel|reels|tv)/(?P<code>[A-Za-z0-9_-]+)")
_EXTRACTOR_TAG = "instagram-authed/0.1"


def shortcode_from_url(url: str) -> Optional[str]:
    """The post/reel shortcode from an IG URL, or None for non-post URLs."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in ("instagram.com", "instagr.am"):
        return None
    match = _SHORTCODE_PATH.match(parsed.path)
    return match.group("code") if match else None


class AuthedInstagramSource:
    """Fetch public post data through an authenticated instagrapi session."""

    def __init__(self, settings: Optional[IngestionSettings] = None, client: Any = None):
        self.settings = settings or IngestionSettings()
        self._client = client  # injectable for tests; real one built lazily

    # ── public API ───────────────────────────────────────────────────────────

    def fetch_url(self, url: str) -> Optional[RawPostSnapshot]:
        code = shortcode_from_url(url)
        if code is None:
            log.warning(f"[ig-authed] not an instagram post url: {url!r}")
            return None
        return self.fetch_shortcode(code)

    def fetch_shortcode(self, shortcode: str) -> Optional[RawPostSnapshot]:
        try:
            client = self._get_client()
            media = client.media_info_by_shortcode(shortcode)
        except Exception as exc:  # LoginRequired/ClientError/network — degrade
            log.warning(f"[ig-authed] fetch failed for {shortcode!r}: {type(exc).__name__}: {exc}")
            return None
        return self._to_snapshot(shortcode, media)

    # ── internals ────────────────────────────────────────────────────────────

    def _get_client(self):
        if self._client is not None:
            return self._client
        from instagrapi import Client  # lazy: optional dep, only on the live path

        settings = self.settings
        if not (settings.ig_username and settings.ig_password):
            raise RuntimeError(
                "authenticated IG fetch needs IG_USERNAME/IG_PASSWORD in the env "
                "(use a dedicated burner account, never a personal one)"
            )
        client = Client()
        session_path = Path(settings.ig_session_path)
        if session_path.exists():
            client.load_settings(session_path)  # reuse cookie — no fresh login
        client.login(settings.ig_username, settings.ig_password)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        client.dump_settings(session_path)
        self._client = client
        return client

    def _to_snapshot(self, shortcode: str, media: Any) -> RawPostSnapshot:
        caption = getattr(media, "caption_text", None) or None
        user = getattr(media, "user", None)
        location = getattr(media, "location", None)
        thumbnail = getattr(media, "thumbnail_url", None)
        video = getattr(media, "video_url", None)

        return RawPostSnapshot(
            source_url=f"https://www.instagram.com/p/{shortcode}/",
            platform="instagram",
            extractor=_EXTRACTOR_TAG,
            fetched_at=datetime.now(timezone.utc),
            caption=caption,
            author_handle=getattr(user, "username", None),
            image_url=str(thumbnail) if thumbnail else None,
            location_text=getattr(location, "name", None),
            # Platform coordinates are authoritative — the geo enricher records
            # them as EXPLICIT and skips Nominatim entirely.
            lat=getattr(location, "lat", None),
            lng=getattr(location, "lng", None),
            hashtags=HASHTAG_RE.findall(caption or ""),
            mentions=MENTION_RE.findall(caption or ""),
            # Extra field (snapshot allows extras): lets the transcriber download
            # the reel's audio without re-fetching the page.
            video_url=str(video) if video else None,
        )
