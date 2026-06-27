"""Diagnostic: where (if anywhere) is a reel's video URL on the logged-out page?"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import truststore

truststore.inject_into_ssl()

from src.ingestion.config import IngestionSettings
from src.ingestion.browser.session_manager import SocialSessionManager

URLS = sys.argv[1:] or [
    "https://www.instagram.com/reel/DYSYFuXsgbH/",
    "https://www.instagram.com/reel/DZpsu1eowNm/",
    "https://www.instagram.com/reel/DZXT8n7p8ME/",
]


async def main():
    settings = IngestionSettings()
    async with SocialSessionManager(settings) as session:
        for url in URLS:
            snap = await session.fetch(url)
            print("=" * 72)
            print(url, "->", snap.status.value)
            meta = snap.meta or {}
            print("meta keys:", sorted(meta.keys()))
            for k, v in meta.items():
                if "video" in k.lower() or "image" in k.lower():
                    print(f"  META {k} = {str(v)[:110]}")
            html = snap.html or ""
            print(f"html length: {len(html)}")
            for pat in ["og:video", '"video_url"', '"contentUrl"', ".mp4", "video_versions", "playable_url"]:
                idx = html.find(pat)
                if idx >= 0:
                    print(f"  HTML has {pat!r}: {html[idx:idx+150].strip()[:150]!r}")
                else:
                    print(f"  HTML has {pat!r}: NO")


asyncio.run(main())
