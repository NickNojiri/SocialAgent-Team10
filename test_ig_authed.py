"""Offline tests for the authenticated Instagram source (fake client, no network).

The real instagrapi client is never imported here — a fake is injected, mirroring
how LlmFieldExtractor/GeoEnricher tests inject their dependencies. The live
measurement run (docs/IG_AUTH_INGESTION_PLAN.md Task 5) lives in test_ig_live.py.
"""

from types import SimpleNamespace

from src.ingestion.config import IngestionSettings
from src.ingestion.sources.ig_authed import AuthedInstagramSource, shortcode_from_url


def fake_media(**overrides):
    defaults = dict(
        caption_text='Late-night birria 🌮 at Casa Loma — this Friday 8pm! #birria #tacos @bitesoflb',
        user=SimpleNamespace(username="bitesoflb"),
        location=SimpleNamespace(name="Casa Loma", lat=33.77, lng=-118.19),
        thumbnail_url="https://cdn.ig/thumb.jpg",
        video_url="https://cdn.ig/v.mp4",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeClient:
    def __init__(self, media=None, exc=None):
        self.media = media
        self.exc = exc
        self.requested: list[str] = []

    def media_info_by_shortcode(self, code):
        self.requested.append(code)
        if self.exc is not None:
            raise self.exc
        return self.media


def make_source(client) -> AuthedInstagramSource:
    return AuthedInstagramSource(IngestionSettings(), client=client)


class TestShortcodeFromUrl:
    def test_post_reel_tv_variants(self):
        assert shortcode_from_url("https://www.instagram.com/reel/DU3evm2Ewhn/?igsh=x") == "DU3evm2Ewhn"
        assert shortcode_from_url("https://instagram.com/p/ABC123/") == "ABC123"
        assert shortcode_from_url("http://www.instagram.com/tv/XYZ789/") == "XYZ789"

    def test_rejects_profiles_and_foreign_hosts(self):
        assert shortcode_from_url("https://www.instagram.com/someuser/") is None
        assert shortcode_from_url("https://example.com/reel/abc/") is None


class TestAuthedSource:
    def test_maps_media_to_snapshot(self):
        raw = make_source(FakeClient(media=fake_media())).fetch_shortcode("DU3evm2Ewhn")
        assert raw is not None
        assert raw.platform == "instagram"
        assert raw.extractor == "instagram-authed/0.1"
        assert raw.source_url == "https://www.instagram.com/p/DU3evm2Ewhn/"
        assert "birria 🌮" in raw.caption          # emoji survives
        assert raw.author_handle == "bitesoflb"
        assert raw.image_url == "https://cdn.ig/thumb.jpg"
        assert raw.location_text == "Casa Loma"
        assert raw.lat == 33.77 and raw.lng == -118.19   # → GeoResolution.EXPLICIT
        assert set(raw.hashtags) == {"birria", "tacos"}
        assert "bitesoflb" in raw.mentions
        assert raw.video_url == "https://cdn.ig/v.mp4"   # extra field for the transcriber
        assert raw.fetched_at is not None

    def test_missing_location_and_media_urls_degrade(self):
        media = fake_media(location=None, thumbnail_url=None, video_url=None)
        raw = make_source(FakeClient(media=media)).fetch_shortcode("ABC")
        assert raw.location_text is None
        assert raw.lat is None and raw.lng is None
        assert raw.image_url is None
        assert raw.video_url is None

    def test_client_error_degrades_to_none(self):
        source = make_source(FakeClient(exc=RuntimeError("login_required")))
        assert source.fetch_shortcode("ABC") is None

    def test_login_failure_is_sticky(self):
        """A failed login must not be retried per-URL (that blacklists the IP)."""
        calls = {"n": 0}

        class FailingLoginSource(AuthedInstagramSource):
            def _get_client(self):
                calls["n"] += 1
                raise RuntimeError("BadPassword")

        source = FailingLoginSource(IngestionSettings())
        assert source.fetch_shortcode("A") is None
        assert source.fetch_shortcode("B") is None
        assert source.fetch_shortcode("C") is None
        assert calls["n"] == 1   # only one login attempt for the whole run

    def test_fetch_url_resolves_shortcode(self):
        client = FakeClient(media=fake_media())
        raw = make_source(client).fetch_url("https://www.instagram.com/reel/DU3evm2Ewhn/?igsh=zz")
        assert raw is not None
        assert client.requested == ["DU3evm2Ewhn"]

    def test_fetch_url_rejects_non_post(self):
        client = FakeClient(media=fake_media())
        assert make_source(client).fetch_url("https://www.instagram.com/someuser/") is None
        assert client.requested == []   # never hit the API for a non-post URL
