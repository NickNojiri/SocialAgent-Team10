"""Live reliability harness for Instagram reel/post ingestion.

Hits real IG URLs over the network and reports the pass rate.
Skip in CI with:  pytest -k "not live"
Run directly  :  pytest test_ig_live.py -v -s --tb=short

Add your own real reel URLs to IG_URLS below before running.
"""

import asyncio
import os
from dataclasses import dataclass

import pytest

from src.ingestion.browser.ig_embed import try_embed_fallback
from src.ingestion.browser.session_manager import SocialSessionManager
from src.ingestion.config import IngestionSettings
from src.ingestion.extractors.instagram import InstagramExtractor
from src.ingestion.schemas.results import FetchStatus

pytestmark = pytest.mark.live

# ── Add real reel/post URLs here before running ──────────────────────────────
IG_URLS: list[str] = [
    # "https://www.instagram.com/reel/REPLACE_ME/",
]

PASS_RATE_TARGET = 0.90   # target: 90% of URLs yield a caption (adjust as you test)

_SETTINGS = IngestionSettings(allow_file_urls=False)
_EXTRACTOR = InstagramExtractor()


@dataclass
class FetchResult:
    url: str
    fetch_status: str
    fallback_used: bool
    caption: str | None
    author: str | None


async def _probe_one(session: SocialSessionManager, url: str) -> FetchResult:
    snapshot = await session.fetch(url)
    fallback_used = False

    if snapshot.status is FetchStatus.LOGIN_WALL:
        recovered = await try_embed_fallback(url)
        if recovered is not None:
            snapshot = recovered
            fallback_used = True

    caption = None
    author = None
    if snapshot.status is FetchStatus.OK:
        raw = _EXTRACTOR.extract(snapshot)
        caption = raw.caption
        author = raw.author_handle

    return FetchResult(
        url=url,
        fetch_status=snapshot.status.value,
        fallback_used=fallback_used,
        caption=caption,
        author=author,
    )


async def _run_all(urls: list[str]) -> list[FetchResult]:
    results: list[FetchResult] = []
    async with SocialSessionManager(_SETTINGS) as session:
        for url in urls:
            result = await _probe_one(session, url)
            status_tag = "[ok]" if result.caption else f"[{result.fetch_status}]"
            fallback_tag = " (embed fallback)" if result.fallback_used else ""
            preview = (result.caption or "")[:80].replace("\n", " ")
            print(f"  {status_tag}{fallback_tag} {url}")
            if preview:
                print(f"    caption: {preview!r}")
            results.append(result)
    return results


def _report(results: list[FetchResult]) -> float:
    total = len(results)
    got_caption = sum(1 for r in results if r.caption)
    login_walled = sum(1 for r in results if r.fetch_status == "login_wall" and not r.fallback_used)
    fallback_saved = sum(1 for r in results if r.fallback_used and r.caption)
    fallback_failed = sum(1 for r in results if r.fallback_used and not r.caption)

    print("\n" + "═" * 60)
    print(" IG LIVE RELIABILITY REPORT")
    print("═" * 60)
    print(f"  URLs tested      : {total}")
    print(f"  Caption obtained : {got_caption}  ({got_caption/total*100:.0f}%)")
    print(f"  Login walled     : {login_walled}  (no fallback recovery)")
    print(f"  Embed saved      : {fallback_saved}  (login wall → embed worked)")
    print(f"  Embed failed     : {fallback_failed}  (login wall + embed failed)")
    print("═" * 60)

    return got_caption / total if total else 0.0


# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not IG_URLS, reason="No IG_URLS defined — add real URLs to test_ig_live.py")
def test_ig_caption_pass_rate():
    """End-to-end: fetch real IG URLs and assert >= PASS_RATE_TARGET get a caption."""
    print(f"\nTesting {len(IG_URLS)} URLs (target: {PASS_RATE_TARGET*100:.0f}% pass rate)\n")
    results = asyncio.run(_run_all(IG_URLS))
    rate = _report(results)
    assert rate >= PASS_RATE_TARGET, (
        f"Pass rate {rate*100:.1f}% is below target {PASS_RATE_TARGET*100:.0f}%. "
        f"Check the printed report above for which URLs failed and why."
    )


@pytest.mark.skipif(not IG_URLS, reason="No IG_URLS defined — add real URLs to test_ig_live.py")
def test_embed_fallback_improves_rate():
    """Confirm the embed fallback recovers at least one login-walled URL."""
    results = asyncio.run(_run_all(IG_URLS))
    walled = [r for r in results if r.fetch_status == "login_wall" or r.fallback_used]
    if not walled:
        pytest.skip("No login walls encountered — nothing to verify fallback against")
    recovered = [r for r in walled if r.fallback_used and r.caption]
    assert recovered, (
        "All walled URLs stayed walled even after embed fallback. "
        "IG may have changed the embed endpoint — check ig_embed.py."
    )
