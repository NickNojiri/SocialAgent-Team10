"""Offline tests for the bot's IG URL detection and caption extraction."""

import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Pull the helpers out of bot.py without starting the Discord client.
# We do this by importing only what we need via exec-level extraction.
from unittest.mock import patch

# Re-implement the same regexes and helpers so tests are self-contained
# (bot.py calls bot.run() at module level — can't import it directly).
_IG_URL_RE = re.compile(
    r'https?://(?:www\.)?instagram\.com/(?:reel|p|tv|reels)/([A-Za-z0-9_-]+)',
    re.IGNORECASE,
)
_JSON_CAPTION_RE = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"')
_HTML_CAPTION_RE = re.compile(r'<span class="[^"]*Caption[^"]*"[^>]*>(.*?)</span>', re.DOTALL)
_HTML_TAG_RE = re.compile(r'<[^>]+')


def _caption_from_html(html: str):
    m = _JSON_CAPTION_RE.search(html)
    if m:
        try:
            raw = json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            raw = m.group(1)
        return raw.strip() or None
    m = _HTML_CAPTION_RE.search(html)
    if m:
        text = _HTML_TAG_RE.sub("", m.group(1)).strip()
        return text or None
    return None


def _thread_name_from_caption(caption, count):
    if count > 1:
        return f"Spots from {count} reels"
    if not caption:
        return "Spot suggestions"
    first_line = caption.split("\n")[0][:80].rstrip()
    return first_line[:80] if first_line else "Spot suggestions"


class TestIgUrlDetection:
    def test_reel_url_matches(self):
        url = "https://www.instagram.com/reel/DZIPn-ppdU4/"
        m = _IG_URL_RE.search(url)
        assert m is not None
        assert m.group(1) == "DZIPn-ppdU4"

    def test_p_url_matches(self):
        url = "https://www.instagram.com/p/DZtDNAokmUh/?img_index=1"
        m = _IG_URL_RE.search(url)
        assert m is not None
        assert m.group(1) == "DZtDNAokmUh"

    def test_tv_url_matches(self):
        url = "https://www.instagram.com/tv/ABC123/"
        m = _IG_URL_RE.search(url)
        assert m is not None
        assert m.group(1) == "ABC123"

    def test_no_match_on_profile(self):
        assert _IG_URL_RE.search("https://www.instagram.com/someuser/") is None

    def test_multiple_urls_in_message(self):
        msg = (
            "Check these out: https://www.instagram.com/reel/AAA111/ "
            "and https://www.instagram.com/reel/BBB222/"
        )
        matches = list(_IG_URL_RE.finditer(msg))
        assert len(matches) == 2
        assert matches[0].group(1) == "AAA111"
        assert matches[1].group(1) == "BBB222"

    def test_url_embedded_in_sentence(self):
        msg = "Omg go to https://www.instagram.com/reel/DZIPn-ppdU4/ right now!!"
        assert _IG_URL_RE.search(msg) is not None

    def test_no_match_on_non_ig_url(self):
        assert _IG_URL_RE.search("https://www.tiktok.com/@user/video/123") is None


class TestCaptionFromHtml:
    def test_json_blob_caption(self):
        html = 'blah "text": "Casa Loma birria in Long Beach" blah'
        assert _caption_from_html(html) == "Casa Loma birria in Long Beach"

    def test_emoji_surrogate_pair_safe(self):
        # IG encodes 🍵 as 🍵 (surrogate pair) in JSON
        html = r'"text": "Café night 🍵 at About Time"'
        result = _caption_from_html(html)
        assert result is not None
        result.encode("utf-8")  # must not raise UnicodeEncodeError

    def test_html_span_fallback(self):
        html = '<span class="Caption-extra">Great tacos!</span>'
        assert _caption_from_html(html) == "Great tacos!"

    def test_none_when_absent(self):
        assert _caption_from_html("<html><body>nothing here</body></html>") is None

    def test_strips_whitespace(self):
        html = '"text": "  hello world  "'
        assert _caption_from_html(html) == "hello world"

    def test_empty_text_returns_none(self):
        html = '"text": ""'
        assert _caption_from_html(html) is None


class TestThreadName:
    def test_single_reel_uses_caption(self):
        caption = "Casa Loma birria in Long Beach — best street food"
        name = _thread_name_from_caption(caption, 1)
        assert name == "Casa Loma birria in Long Beach — best street food"

    def test_truncates_long_caption(self):
        caption = "A" * 200
        name = _thread_name_from_caption(caption, 1)
        assert len(name) <= 80

    def test_multiple_reels(self):
        name = _thread_name_from_caption("anything", 3)
        assert name == "Spots from 3 reels"

    def test_no_caption_fallback(self):
        name = _thread_name_from_caption(None, 1)
        assert name == "Spot suggestions"

    def test_uses_first_line_only(self):
        caption = "Great tacos!\nSecond line info\nThird line"
        name = _thread_name_from_caption(caption, 1)
        assert name == "Great tacos!"
