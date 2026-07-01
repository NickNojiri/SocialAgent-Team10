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
# Tolerates straight and typographic quotes around the caption. The trailing
# class tolerates IG's '". ' ending (closing quote + period + space) — anchoring
# strictly on the quote made the whole preamble leak into the caption.
_OG_DESC = re.compile(
    r'-\s*(?P<handle>[\w.]+)\s+on\s+[^:]+:\s*["“](?P<caption>.+)["”][\s.]*$',
    re.DOTALL,
)
# Fallback: strip the "N likes, M comments - handle on DATE:" preamble (and the
# post date in it) when the caption is truncated and the full pattern can't match.
_DESC_PREAMBLE = re.compile(
    r'^\s*[\d,]+\s+likes?,\s*[\d,]+\s+comments?\s*-\s*[\w.]+\s+on\s+[^:]+:\s*["“]?',
    re.IGNORECASE,
)
_OG_TITLE_HANDLE = re.compile(r"@([\w.]+)")
# "<name> on Instagram: ..." — author/caption boilerplate, not a venue title.
_AUTHOR_TITLE = re.compile(r"\bon\s+Instagram\b", re.IGNORECASE)


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
                # Format drifted (e.g. caption truncated, no closing quote) — strip
                # the likes/comments/handle/date preamble so the post's own metadata
                # (especially its publish date) can't pollute the caption downstream.
                caption = caption or _DESC_PREAMBLE.sub("", og_description).strip()

        og_title = snapshot.meta.get("og:title") or ""
        if author is None:
            title_match = _OG_TITLE_HANDLE.search(og_title)
            author = title_match.group(1) if title_match else None

        # og:title is a usable venue title for a venue's *own* account
        # ("Casa Loma (@x) • Instagram"), but for a person's post/reel it is
        # author+caption boilerplate ("Adrian on Instagram: \"…\"") that must not
        # be mistaken for a venue. Drop it in that case; the caption carries the
        # real content and the LLM/heuristics pull the venue from there.
        title = og_title or None
        if title and _AUTHOR_TITLE.search(title):
            title = None

        text_pool = " ".join(filter(None, [caption, og_title]))

        return RawPostSnapshot(
            source_url=snapshot.url,
            platform="instagram",
            extractor=f"{self.name}/{self.version}",
            fetched_at=snapshot.fetched_at,
            caption=caption,
            title=title,
            # The caption (above) is the real content; og:description is just its
            # noisy wrapper ("N likes, M comments - handle on DATE: …"). Dropping it
            # keeps the post's publish date out of time extraction and the LLM payload.
            description=None,
            author_handle=author,
            image_url=snapshot.meta.get("og:image") or snapshot.meta.get("og:image:url"),
            location_text=snapshot.first_text(TextRole.LOCATION_TAG),
            hashtags=HASHTAG_RE.findall(text_pool),
            mentions=MENTION_RE.findall(caption or ""),
        )
