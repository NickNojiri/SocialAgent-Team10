"""Instagram public post/reel adapter — logged-out view only.

Logged-out Instagram frequently withholds content behind a login wall; that is
an expected outcome (FetchStatus.LOGIN_WALL upstream), not something this
adapter works around. When the public page *is* served, the caption excerpt
reliably appears in og:description, shaped like:

    120 likes, 8 comments - handle on June 5, 2026: "caption text"
"""

import re
from urllib.parse import urlparse

from src.ingestion.extractors.base import HASHTAG_RE, MENTION_RE
from src.ingestion.schemas.snapshot import PageSnapshot, RawPostSnapshot, TextRole

_POST_PATH = re.compile(r"^/(p|reel|reels|tv)/")
# Tolerates straight and typographic quotes around the caption.
_OG_DESC = re.compile(
    r'-\s*(?P<handle>[\w.]+)\s+on\s+[^:]+:\s*["“](?P<caption>.+)["”]\s*$',
    re.DOTALL,
)
_OG_TITLE_HANDLE = re.compile(r"@([\w.]+)")


class InstagramExtractor:
    name = "instagram"
    version = "0.1"

    def claims(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        return host in ("instagram.com", "instagr.am") and bool(_POST_PATH.match(parsed.path))

    def extract(self, snapshot: PageSnapshot) -> RawPostSnapshot:
        # Rendered caption beats the og: excerpt when text isolation found one.
        caption = snapshot.first_text(TextRole.CAPTION)
        author = None

        og_description = snapshot.meta.get("og:description")
        if og_description:
            match = _OG_DESC.search(og_description)
            if match:
                author = match.group("handle")
                caption = caption or match.group("caption")
            else:
                # Format drifted — keep the raw excerpt rather than dropping it.
                caption = caption or og_description

        og_title = snapshot.meta.get("og:title") or ""
        if author is None:
            title_match = _OG_TITLE_HANDLE.search(og_title)
            author = title_match.group(1) if title_match else None

        text_pool = " ".join(filter(None, [caption, og_title]))

        return RawPostSnapshot(
            source_url=snapshot.url,
            platform="instagram",
            extractor=f"{self.name}/{self.version}",
            fetched_at=snapshot.fetched_at,
            caption=caption,
            title=og_title or None,
            description=og_description,
            author_handle=author,
            location_text=snapshot.first_text(TextRole.LOCATION_TAG),
            hashtags=HASHTAG_RE.findall(text_pool),
            mentions=MENTION_RE.findall(caption or ""),
        )
