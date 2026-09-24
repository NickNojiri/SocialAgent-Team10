"""Offline tests for the reel-capture cards (no network, no Discord gateway).

Run from the repo root:  pytest app/test_cards.py -v
"""

from types import SimpleNamespace

import pytest

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


def test_extract_capture_urls_includes_tiktok():
    text = (
        "https://www.tiktok.com/@user/video/7301234567890123456?_t=8k "
        "https://vm.tiktok.com/ZMabcDEF/ "
        "https://www.instagram.com/reel/DZXT8n7p8ME/"
    )
    urls = cards.extract_capture_urls(text)
    assert "https://www.instagram.com/reel/DZXT8n7p8ME/" in urls
    assert "https://www.tiktok.com/@user/video/7301234567890123456/" in urls
    assert "https://vm.tiktok.com/ZMabcDEF/" in urls
    # the IG-only helper still filters correctly
    assert cards.extract_ig_urls(text) == ["https://www.instagram.com/reel/DZXT8n7p8ME/"]


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
    # no coords → a map search for the venue instead of a pin, and no distance (#36)
    assert fields["Where"] == "[Find on map](https://www.openstreetmap.org/search?query=Cafe+X)"


# ── distance + map (#36) ─────────────────────────────────────────────────────

LONG_BEACH = {"home_city": "Long Beach, CA", "home_lat": 33.7701, "home_lng": -118.1937}


def _where(event, home=None):
    return {f.name: f.value for f in cards.build_spot_embed(event, home).fields}.get("Where")


def test_distance_from_the_home_city_when_both_places_are_known():
    # Santa Monica Pier is ~24 miles from downtown Long Beach as the crow flies
    where = _where({"venue": "Pier", "category": "outdoors", "lat": 34.0092, "lng": -118.4976}, LONG_BEACH)
    assert where.startswith("[Open map](https://www.openstreetmap.org/?mlat=34.0092")
    assert where.endswith(" · 24 mi from Long Beach, CA")


def test_short_distances_keep_a_decimal():
    near = {"venue": "Cafe", "category": "cafe_dessert", "lat": 33.7801, "lng": -118.1937}
    assert _where(near, LONG_BEACH).endswith(" · 0.7 mi from Long Beach, CA")
    same = {"venue": "Cafe", "category": "cafe_dessert", "lat": 33.7701, "lng": -118.1937}
    assert _where(same, LONG_BEACH).endswith(" · under 0.1 mi from Long Beach, CA")


@pytest.mark.parametrize("home", [None, {"home_city": "Long Beach, CA", "home_lat": None, "home_lng": None}])
def test_no_distance_without_a_located_home_city(home):
    where = _where({"venue": "Pier", "category": "outdoors", "lat": 34.0, "lng": -118.5}, home)
    assert where == "[Open map](https://www.openstreetmap.org/?mlat=34.0&mlon=-118.5#map=17/34.0/-118.5)"


def test_no_coordinates_degrades_to_a_search_near_the_posts_area():
    event = {"venue": "Casa Loma", "category": "food_drink", "area": "123 Pine Ave, Long Beach, CA 90802"}
    assert _where(event, LONG_BEACH) == (
        "[Find on map](https://www.openstreetmap.org/search?query=Casa+Loma%2C+Long+Beach)")


def test_no_coordinates_and_no_area_searches_near_the_home_city():
    event = {"venue": "Casa Loma", "category": "food_drink"}
    assert "query=Casa+Loma%2C+Long+Beach%2C+CA" in _where(event, LONG_BEACH)


def test_nothing_to_map_means_no_field():
    assert _where({"venue": "Unknown", "category": "other"}) is None
    assert _where({"category": "other"}) is None


def test_the_home_city_cant_inject_markdown_and_the_query_is_encoded():
    home = {**LONG_BEACH, "home_city": "[click](https://evil.test)"}
    where = _where({"venue": "A)B", "category": "other", "lat": 34.0, "lng": -118.5}, home)
    # The escaped "[" means Discord can't turn it into a disguised link.
    assert "from \\[click](https://evil.test)" in where
    assert "A%29B" in _where({"venue": "A)B", "category": "other"})


@pytest.mark.asyncio
async def test_home_for_is_quiet_when_it_cant_help(monkeypatch):
    async def found(guild):
        return LONG_BEACH

    async def broken(guild):
        raise RuntimeError("admin app down")

    monkeypatch.setattr(cards, "HOME_LOOKUP", found)
    assert await cards.home_for("99") == LONG_BEACH
    assert await cards.home_for("dm-7") is None             # a DM stash has no home city
    monkeypatch.setattr(cards, "HOME_LOOKUP", broken)
    assert await cards.home_for("99") is None               # a card never fails over distance
    monkeypatch.setattr(cards, "HOME_LOOKUP", None)
    assert await cards.home_for("99") is None


@pytest.mark.asyncio
async def test_a_captured_card_shows_distance_from_the_servers_city(monkeypatch):
    import bot
    import setup_wizard

    async def settings(guild, *, fresh=False):
        return LONG_BEACH

    monkeypatch.setattr(setup_wizard, "settings_for", settings)
    edits = []

    async def edit(**kwargs):
        edits.append(kwargs)

    async def noop(*a, **k):
        return None

    message = SimpleNamespace(guild=SimpleNamespace(id=99), author=SimpleNamespace(display_name="nick", id=7),
                              add_reaction=noop, remove_reaction=noop)
    monkeypatch.setattr(bot, "_maybe_first_card_tip", noop)
    event = {"id": "e1", "venue": "Pier", "category": "outdoors", "lat": 34.0092, "lng": -118.4976}
    await bot._render_capture(message, SimpleNamespace(edit=edit), ["https://x.test/1"], {"events": [event]})
    where = {f.name: f.value for f in edits[0]["embed"].fields}["Where"]
    assert where.endswith("24 mi from Long Beach, CA")


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
