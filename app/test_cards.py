"""Offline tests for the reel-capture cards (no network, no Discord gateway).

Run from the repo root:  pytest app/test_cards.py -v
"""

from types import SimpleNamespace

import cards


def test_guild_key_server_dm_and_fallback():
    server_msg = SimpleNamespace(guild=SimpleNamespace(id=123), author=SimpleNamespace(id=9))
    assert cards.guild_key(server_msg) == "123"
    dm_msg = SimpleNamespace(guild=None, author=SimpleNamespace(id=9))
    assert cards.guild_key(dm_msg) == "dm-9"          # DMs → personal stash
    dm_interaction = SimpleNamespace(guild=None, user=SimpleNamespace(id=7))
    assert cards.guild_key(dm_interaction) == "dm-7"  # interactions use .user
    assert cards.guild_key(SimpleNamespace(guild=None)) == ""  # legacy fallback


def test_extract_ig_urls_strips_tracking_params():
    urls = cards.extract_ig_urls(
        "check this https://www.instagram.com/reel/DZXT8n7p8ME/?igsh=abc123 out"
    )
    assert urls == ["https://www.instagram.com/reel/DZXT8n7p8ME/"]


def test_extract_ig_urls_variants_and_dedup():
    text = (
        "https://instagram.com/p/ABC123/ "
        "http://www.instagram.com/tv/XYZ789/ "
        "https://www.instagram.com/reel/DZXT8n7p8ME/ "
        "https://www.instagram.com/reel/DZXT8n7p8ME/?igsh=zzz "  # duplicate of the previous
    )
    assert cards.extract_ig_urls(text) == [
        "https://instagram.com/p/ABC123/",
        "http://www.instagram.com/tv/XYZ789/",
        "https://www.instagram.com/reel/DZXT8n7p8ME/",
    ]


def test_extract_ig_urls_ignores_non_posts():
    assert cards.extract_ig_urls("no links here") == []
    assert cards.extract_ig_urls("https://example.com/reel/abc/") == []
    assert cards.extract_ig_urls("https://www.instagram.com/someuser/") == []  # profile, not a post


def test_build_spot_embed_scheduled():
    event = {
        "id": "abc",
        "venue": "Nikushou Nakata Honten",
        "category": "food_drink",
        "theme": "top-grade wagyu yakiniku",
        "source_url": "https://www.instagram.com/reel/DZXT8n7p8ME/",
        "image": "https://cdn.ig/thumb.jpg",
        "lat": 35.17,
        "lng": 136.91,
        "start_epoch": 1781000000,
        "end_epoch": 1781007200,
        "votes": 3,
        "sharer": "nick",
    }
    event["voters"] = ["nick", "sam"]
    embed = cards.build_spot_embed(event)
    assert "Nikushou Nakata Honten" in embed.title
    assert embed.title.startswith("🍽️")  # food_drink emoji
    assert embed.url == event["source_url"]
    assert embed.thumbnail.url == "https://cdn.ig/thumb.jpg"
    fields = {f.name: f.value for f in embed.fields}
    assert "Category" not in fields          # v3: category lives in emoji + footer
    assert "<t:1781000000:F>" in fields["When"]
    assert fields["Where"] == (
        "[Open map](https://www.openstreetmap.org/?mlat=35.17&mlon=136.91#map=17/35.17/136.91)"
    )
    assert fields["Who's in"] == "nick, sam"
    assert embed.footer.text == "food drink · shared by nick · via Instagram"


def test_build_spot_embed_unscheduled_and_already():
    event = {"id": "x", "venue": "Cafe X", "category": "cafe_dessert", "already": True, "votes": 5}
    embed = cards.build_spot_embed(event)
    fields = {f.name: f.value for f in embed.fields}
    assert "When" not in fields              # v3: no date → no noisy field
    assert any("Already in the catalog" in f.value for f in embed.fields)
    assert embed.thumbnail.url is None       # no image → no thumbnail
    assert "Where" not in fields             # no coords → no map field


def test_platform_label_from_source_url():
    assert cards._platform_label("https://www.tiktok.com/@x/video/1") == "via TikTok"
    assert cards._platform_label("https://www.instagram.com/reel/X/") == "via Instagram"
    assert cards._platform_label("") == "added manually"


def test_with_whos_in_adds_replaces_and_clears():
    embed = cards.build_spot_embed({"id": "x", "venue": "Cafe X", "category": "other"})
    cards.with_whos_in(embed, ["nick", "sam"])
    assert {f.name: f.value for f in embed.fields}["Who's in"] == "nick, sam"
    cards.with_whos_in(embed, ["nick"])   # replaces in place, never duplicates
    whos_in = [f for f in embed.fields if f.name == "Who's in"]
    assert len(whos_in) == 1 and whos_in[0].value == "nick"
    cards.with_whos_in(embed, [])
    assert all(f.name != "Who's in" for f in embed.fields)


def test_default_start_time_is_a_future_friday_evening():
    from datetime import datetime, timezone

    start = cards.default_start_time()
    assert start.weekday() == 4          # Friday
    assert start.hour == 19
    assert start > datetime.now(timezone.utc)


def test_lock_in_button_custom_id():
    button = cards.LockInButton("deadbeef")
    assert button.custom_id == "spot:lockin:deadbeef"


def test_failure_view_composition():
    url = "https://www.instagram.com/reel/DU3evm2Ewhn/"
    view = cards.build_failure_view(url)
    ids = [child.custom_id for child in view.children]
    assert ids == [f"spot:retry:{url}", "spot:manual:0"]

    # multi-link failures (no single url) and over-long urls get manual-add only
    assert [c.custom_id for c in cards.build_failure_view(None).children] == ["spot:manual:0"]
    long_url = "https://www.instagram.com/reel/" + "x" * 90 + "/"
    assert [c.custom_id for c in cards.build_failure_view(long_url).children] == ["spot:manual:0"]


def test_build_spot_view_has_five_buttons_with_event_id():
    view = cards.build_spot_view("deadbeef", votes=2)
    custom_ids = [child.custom_id for child in view.children]
    assert custom_ids == [
        "spot:vote:1:deadbeef",
        "spot:vote:-1:deadbeef",
        "spot:suggest:deadbeef",
        "spot:edit:deadbeef",
        "spot:remove:deadbeef",
    ]
