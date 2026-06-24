"""Instagram embed-page fallback for login-walled fetches.

When the main IG page returns a LOGIN_WALL, the /embed/ URL often serves the
caption without requiring authentication. This module attempts that recovery and
returns a minimal PageSnapshot the rest of the pipeline can use as-is.

No Playwright needed — the embed page is a simple server-rendered HTML response.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from src.ingestion.schemas.results import FetchStatus
from src.ingestion.schemas.snapshot import PageSnapshot

log = logging.getLogger("ingestion.ig_embed")

_SHORTCODE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")

# Caption appears in two known places in the embed HTML:
# 1. Inline JSON: "edge_media_to_caption":{"edges":[{"node":{"text":"..."}}]}
# 2. Visible span: <span class="Caption"><span>...</span></span>
_JSON_CAPTION = re.compile(
    r'"edge_media_to_caption"\s*:\s*\{"edges"\s*:\s*\[.*?"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
    re.DOTALL,
)
_HTML_CAPTION = re.compile(
    r'<span[^>]*class="[^"]*Caption[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
_HTML_TAG = re.compile(r"<[^>]+>")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 15.0


def _shortcode(url: str) -> Optional[str]:
    m = _SHORTCODE.search(urlparse(url).path)
    return m.group(1) if m else None


def _caption_from_html(html: str) -> Optional[str]:
    m = _JSON_CAPTION.search(html)
    if m:
        # Decode as a real JSON string: this correctly recombines \uXXXX surrogate
        # PAIRS (emoji) into single codepoints. `unicode_escape` decodes each half
        # independently, leaving lone surrogates that crash on UTF-8 encode when the
        # caption is later written to the JSONL sink — and IG captions are full of emoji.
        try:
            raw = json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            raw = m.group(1)
        return raw.strip() or None

    m = _HTML_CAPTION.search(html)
    if m:
        text = _HTML_TAG.sub("", m.group(1)).strip()
        return text or None

    return None


def _og_meta(html: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for prop, content in re.findall(
        r'<meta[^>]+property=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    ):
        meta[prop] = content
    return meta


async def try_embed_fallback(original_url: str) -> Optional[PageSnapshot]:
    """Fetch the /embed/captioned/ page and return a PageSnapshot, or None on failure."""
    code = _shortcode(original_url)
    if not code:
        log.debug(f"[embed] no shortcode in {original_url!r}, skipping fallback")
        return None

    # /reels/ shortcodes use /p/ in the embed URL
    embed_url = f"https://www.instagram.com/p/{code}/embed/captioned/"
    log.info(f"[embed] LOGIN_WALL on main page — trying {embed_url}")

    try:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=_TIMEOUT) as client:
            resp = await client.get(embed_url)
    except httpx.RequestError as exc:
        log.warning(f"[embed] request error: {exc}")
        return None

    if resp.status_code != 200:
        log.info(f"[embed] HTTP {resp.status_code} — fallback gave up")
        return None

    html = resp.text
    caption = _caption_from_html(html)
    meta = _og_meta(html)

    if not caption and not meta:
        log.info("[embed] no caption or meta found in embed page")
        return None

    # Synthesise an og:description in the same format the extractor expects so
    # downstream parsing is identical to the non-walled path.
    if caption and "og:description" not in meta:
        meta["og:description"] = f'0 likes, 0 comments - unknown on unknown: "{caption}"'

    log.info(f"[embed] recovered caption ({len(caption or '')} chars) via embed fallback")
    return PageSnapshot(
        url=original_url,
        status=FetchStatus.OK,
        fetched_at=datetime.now(timezone.utc),
        final_url=embed_url,
        meta=meta,
        html=html,
    )
