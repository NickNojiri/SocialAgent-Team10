"""Offline tests for the TikTok adapter — handmade og: snapshots, no network."""

from datetime import datetime, timezone

from src.ingestion.extractors.base import select_extractor
from src.ingestion.extractors.tiktok import TikTokExtractor
from src.ingestion.pipeline.validator import build_record
from src.ingestion.schemas.results import FetchStatus
from src.ingestion.schemas.snapshot import PageSnapshot

NOW = datetime.now(timezone.utc)
TT_URL = "https://www.tiktok.com/@bitesoflb/video/7300000000000000000"
TT_CAPTION = "hidden birria spot at Casa Loma in Long Beach 🌮 #birria #LongBeachFood"


def snap(**overrides) -> PageSnapshot:
    defaults = dict(url=TT_URL, status=FetchStatus.OK, fetched_at=NOW)
    defaults.update(overrides)
    return PageSnapshot(**defaults)


class TestClaims:
    def test_video_urls_claimed(self):
        e = TikTokExtractor()
        assert e.claims(TT_URL)
        assert e.claims("https://www.tiktok.com/t/ZTabc123/")
        assert e.claims("https://vm.tiktok.com/ZMabc/")
        assert e.claims("https://vt.tiktok.com/ZSxyz/")

    def test_non_videos_not_claimed(self):
        e = TikTokExtractor()
        assert not e.claims("https://www.tiktok.com/@someuser")
        assert not e.claims("https://example.com/@x/video/1")

    def test_router_prefers_tiktok_over_generic(self):
        assert select_extractor(TT_URL).name == "tiktok"
        assert select_extractor("https://www.instagram.com/reel/X/").name == "instagram"


class TestExtract:
    def test_og_tags_to_snapshot(self):
        raw = TikTokExtractor().extract(
            snap(meta={
                "og:title": "Bites of LB (@bitesoflb) on TikTok",
                "og:description": f"228.4K Likes, 512 Comments. {TT_CAPTION} | TikTok",
                "og:image": "https://p16.tiktokcdn.com/thumb.jpg",
                "og:video": "https://v16.tiktokcdn.com/v.mp4",
            })
        )
        assert raw.platform == "tiktok"
        assert raw.caption == TT_CAPTION
        assert raw.author_handle == "bitesoflb"
        assert raw.title is None                       # "on TikTok" boilerplate dropped
        assert raw.image_url == "https://p16.tiktokcdn.com/thumb.jpg"
        assert raw.video_url == "https://v16.tiktokcdn.com/v.mp4"
        assert set(raw.hashtags) >= {"birria", "LongBeachFood"}

    def test_author_falls_back_to_url_handle(self):
        raw = TikTokExtractor().extract(snap(meta={"og:description": TT_CAPTION}))
        assert raw.author_handle == "bitesoflb"

    def test_end_to_end_record_keeps_tiktok_platform(self):
        raw = TikTokExtractor().extract(snap(meta={"og:description": TT_CAPTION}))
        record, reason = build_record(raw)
        assert reason is None
        assert record.provenance.platform == "tiktok"
        assert record.venue_name == "Casa Loma"        # heuristic "at X" still works
