"""Offline unit tests for the Instagram embed-page fallback parser.

These are network-free (they feed HTML strings straight into the parser) so they
run in CI. The live fetch path is exercised separately in test_ig_live.py.
"""

import pytest

from src.ingestion.browser.ig_embed import (
    _caption_from_html,
    _og_meta,
    _shortcode,
)

CAPTION = "Late-night birria tacos pop-up at Casa Loma in Long Beach this Friday 8pm"


class TestShortcode:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.instagram.com/reel/DZIPn-ppdU4/?igsh=x", "DZIPn-ppdU4"),
            ("https://www.instagram.com/p/DZtDNAokmUh/?img_index=1", "DZtDNAokmUh"),
            ("https://instagram.com/tv/ABC-123_x/", "ABC-123_x"),
            ("https://www.instagram.com/reels/DZpsu1eowNm/", "DZpsu1eowNm"),
        ],
    )
    def test_extracts_shortcode(self, url, expected):
        assert _shortcode(url) == expected

    def test_returns_none_for_profile_url(self):
        assert _shortcode("https://www.instagram.com/someuser/") is None


class TestCaptionFromHtml:
    def test_inline_json_edges(self):
        html = (
            '<script>"edge_media_to_caption":{"edges":[{"node":{"text":"'
            + CAPTION
            + '"}}]}</script>'
        )
        assert _caption_from_html(html) == CAPTION

    def test_visible_caption_span(self):
        html = (
            '<div class="Caption"><a class="CaptionUsername">bitesoflb</a>'
            '<span class="Caption" dir="auto"><span>' + CAPTION + "</span></span></div>"
        )
        assert _caption_from_html(html) == CAPTION

    def test_emoji_surrogate_pair_decodes_and_is_utf8_safe(self):
        """Regression: \\uD83C\\uDF75 (🍵) must recombine into one codepoint.

        unicode_escape leaves lone surrogates that crash on .encode('utf-8') when
        the caption hits the JSONL sink — and real IG captions are full of emoji.
        """
        html = (
            '<script>"edge_media_to_caption":{"edges":[{"node":{"text":'
            '"Caf\\u00e9 night \\ud83c\\udf75 at About Time"}}]}</script>'
        )
        caption = _caption_from_html(html)
        assert caption == "Café night 🍵 at About Time"
        # The real assertion: this must not raise UnicodeEncodeError.
        caption.encode("utf-8")

    def test_escaped_quotes_in_caption(self):
        html = (
            '<script>"edge_media_to_caption":{"edges":[{"node":{"text":'
            '"She said \\"best tacos ever\\" honestly"}}]}</script>'
        )
        assert _caption_from_html(html) == 'She said "best tacos ever" honestly'

    def test_returns_none_when_no_caption(self):
        assert _caption_from_html("<html><body>nothing here</body></html>") is None


class TestOgMeta:
    def test_parses_og_properties(self):
        html = (
            '<meta property="og:description" content="120 likes - bitesoflb: hi">'
            '<meta property="og:title" content="Bites of LB on Instagram">'
        )
        meta = _og_meta(html)
        assert meta["og:description"] == "120 likes - bitesoflb: hi"
        assert meta["og:title"] == "Bites of LB on Instagram"

    def test_empty_when_no_meta(self):
        assert _og_meta("<html><body></body></html>") == {}
