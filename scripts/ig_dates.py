"""When was a reel posted? Decoded from its URL alone, offline.

Instagram media IDs carry their creation time (41 bits of milliseconds since
Instagram's 2011 epoch, then shard and sequence bits), and a reel's shortcode is
that ID in base64. So the post date needs no fetch, no login and no API.

Labels need it for deals and events: "through Sept 27" only has a year once you
know when the post went up.

    python scripts/ig_dates.py https://www.instagram.com/reel/DdCKKc1p-7K/
    -> 2026-09-08
"""
from __future__ import annotations

import datetime as dt
import sys

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_IG_EPOCH_MS = 1314220021721            # 2011-08-24, from Instagram's ID scheme


def _shortcode(url_or_code: str) -> str:
    parts = [p for p in url_or_code.split("?")[0].split("/") if p]
    return parts[-1] if parts else ""


def posted_at(url_or_code: str) -> dt.datetime | None:
    """UTC time the post was created, or None if the code isn't a media shortcode."""
    code = _shortcode(url_or_code)[:11]   # longer private/story codes carry extra chars
    if len(code) < 10 or any(ch not in _ALPHABET for ch in code):
        return None
    media_id = 0
    for ch in code:
        media_id = media_id * 64 + _ALPHABET.index(ch)
    ms = (media_id >> 23) + _IG_EPOCH_MS
    when = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return when if dt.datetime(2011, 8, 1, tzinfo=dt.timezone.utc) < when else None


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        when = posted_at(arg)
        print(when.date().isoformat() if when else f"not a media link: {arg}")
