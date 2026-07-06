"""TikTok public video adapter — logged-out og: tags, mirroring the IG adapter.

TikTok's logged-out video page carries the caption in og:description (often
with a " | TikTok" suffix or a stats preamble) and the author in og:title
("name on TikTok" or "name (@handle) | TikTok"). Like Instagram, walls are an
expected outcome upstream, never worked around.
"""

import re
from urllib.parse import urlparse

from src.ingestion.extractors.base import HASHTAG_RE, MENTION_RE
from src.ingestion.schemas.snapshot import PageSnapshot, RawPostSnapshot, TextRole

_VIDEO_PATH = re.compile(r"^/(@[\w.]+/video/\d+|t/[\w]+|v/\d+)")
_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")
_ON_TIKTOK = re.compile(r"\bon TikTok\b|\|\s*TikTok\s*$", re.IGNORECASE)
_HANDLE_IN_URL = re.compile(r"/@([\w.]+)/")
_HANDLE_IN_TITLE = re.compile(r"@([\w.]+)")
# "123.4K Likes, 512 Comments. caption text" stats preamble some pages emit.
_STATS_PREAMBLE = re.compile(r"^[\d.,KMB]+\s+Likes?,\s*[\d.,KMB]+\s+Comments?\.\s*", re.IGNORECASE)


class TikTokExtractor:
    name = "tiktok"
    version = "0.1"

    def claims(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        if host in _SHORT_HOSTS:
            return True   # share short-links redirect to a video
        return host == "tiktok.com" and bool(_VIDEO_PATH.match(parsed.path))

    def extract(self, snapshot: PageSnapshot) -> RawPostSnapshot:
        caption = snapshot.first_text(TextRole.CAPTION)

        og_description = snapshot.meta.get("og:description") or ""
        if not caption and og_description:
            cleaned = _STATS_PREAMBLE.sub("", og_description)
            cleaned = _ON_TIKTOK.sub("", cleaned).strip(" |")
            caption = cleaned or None

        og_title = snapshot.meta.get("og:title") or ""
        title_match = _HANDLE_IN_TITLE.search(og_title)
        url_match = _HANDLE_IN_URL.search(snapshot.final_url or snapshot.url)
        author = (title_match or url_match).group(1) if (title_match or url_match) else None

        # "name on TikTok" is author boilerplate, not a venue title.
        title = og_title or None
        if title and _ON_TIKTOK.search(title):
            title = None

        text_pool = " ".join(filter(None, [caption, og_title]))

        return RawPostSnapshot(
            source_url=snapshot.url,
            platform="tiktok",
            extractor=f"{self.name}/{self.version}",
            fetched_at=snapshot.fetched_at,
            caption=caption,
            title=title,
            description=None,   # og:description is the caption's noisy wrapper
            author_handle=author,
            image_url=snapshot.meta.get("og:image") or snapshot.meta.get("og:image:url"),
            location_text=snapshot.first_text(TextRole.LOCATION_TAG),
            hashtags=HASHTAG_RE.findall(text_pool),
            mentions=MENTION_RE.findall(caption or ""),
            video_url=snapshot.meta.get("og:video") or snapshot.meta.get("og:video:secure_url"),
        )
