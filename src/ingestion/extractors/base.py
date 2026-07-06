"""Adapter protocol and registry.

An extractor turns a PageSnapshot (whatever the browser captured) into a
RawPostSnapshot (best-effort fields). It runs purely on captured data — no
page handle, no network — which keeps adapters trivially testable offline.
"""

import re
from typing import Protocol

from src.ingestion.schemas.snapshot import PageSnapshot, RawPostSnapshot

HASHTAG_RE = re.compile(r"#(\w+)")
MENTION_RE = re.compile(r"@([\w.]+)")


class BaseExtractor(Protocol):
    name: str
    version: str

    def claims(self, url: str) -> bool: ...

    def extract(self, snapshot: PageSnapshot) -> RawPostSnapshot: ...


def select_extractor(url: str) -> BaseExtractor:
    # Imported here to avoid a circular import at module load.
    from src.ingestion.extractors.generic import GenericExtractor
    from src.ingestion.extractors.instagram import InstagramExtractor
    from src.ingestion.extractors.tiktok import TikTokExtractor

    for extractor in (InstagramExtractor(), TikTokExtractor(), GenericExtractor()):
        if extractor.claims(url):
            return extractor
    return GenericExtractor()
