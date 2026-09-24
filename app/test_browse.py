"""Offline tests for /browse filters (Track C #35).

Done-when: the filters work on a server with 50+ spots without a wall of text.
So: category, area and "has a date" narrow correctly, alone and together; the
area autocomplete offers this server's own areas; and a page of a 60-spot
catalog stays a short, scannable list.
"""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from discord import app_commands

import bot
import cards

pytestmark = pytest.mark.asyncio

FRI_OCT_2 = int(datetime(2026, 10, 2, 19, 0, tzinfo=ZoneInfo(cards.BOT_TZ)).timestamp())


def _spot(i, category="food_drink", area="", **extra):
    return {"id": f"e{i}", "venue": f"Spot {i}", "category": category, "area": area,
            "votes": i % 7, "source_url": f"https://www.instagram.com/p/{i}/",
            "blurb": "a long enough vibe description to look like the real thing " * 2, **extra}


def _catalog(n=60):
    areas = ["123 Pine Ave, Long Beach, CA 90802", "Santa Ana, CA", "Downtown LA, Los Angeles, CA", ""]
    kinds = ["food_drink", "nightlife", "cafe_dessert"]
    return [_spot(i, kinds[i % 3], areas[i % 4], **({"start_epoch": FRI_OCT_2} if i % 5 == 0 else {}))
            for i in range(n)]


# ── the pieces ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, parts", [
    ("123 Pine Ave, Long Beach, CA 90802", ["Long Beach"]),
    ("Santa Ana, CA", ["Santa Ana"]),
    ("Downtown LA, Los Angeles, CA, USA", ["Downtown LA", "Los Angeles"]),
    ("", []),
    ("90802", []),
])
async def test_area_parts_drop_addresses_states_zips_and_country(text, parts):
    assert cards.area_parts(text) == parts


async def test_suggestions_are_this_servers_areas_most_spots_first():
    events = [_spot(1, area="Long Beach, CA"), _spot(2, area="long beach"),
              _spot(3, area="Santa Ana, CA"), _spot(4, area="1 Main St, Irvine, CA 92618")]
    assert cards.area_suggestions(events) == ["Long Beach", "Irvine", "Santa Ana"]
    assert cards.area_suggestions(events, "  SANTA ") == ["Santa Ana"]
    assert cards.area_suggestions(events, "nowhere") == []


@pytest.mark.parametrize("extra, dated", [
    ({}, False),
    ({"start_epoch": FRI_OCT_2}, True),               # the reel named a date
    ({"locked_end_epoch": FRI_OCT_2}, True),          # the group locked it in
    ({"schedule": "scheduled"}, True),
])
async def test_has_a_date(extra, dated):
    assert cards.has_date(_spot(1, **extra)) is dated


async def test_filters_narrow_alone_and_together():
    events = _catalog()
    assert len(cards.filter_spots(events)) == 60
    assert all(e["category"] == "nightlife" for e in cards.filter_spots(events, category="nightlife"))
    assert len(cards.filter_spots(events, category="all")) == 60
    lb = cards.filter_spots(events, area="  long   BEACH ")
    assert lb and all("Long Beach" in e["area"] for e in lb)
    both = cards.filter_spots(events, category="food_drink", area="Long Beach", dated=True)
    assert both and all(e["category"] == "food_drink" and cards.has_date(e) for e in both)
    undated = cards.filter_spots(events, dated=False)
    assert undated and not any(cards.has_date(e) for e in undated)


async def test_a_page_of_sixty_spots_is_a_short_scannable_list():
    events = _catalog()
    embed = cards.build_browse_embed(events, 0, label="🍽️ food & drink", of_total=90)
    rows = embed.description.split("\n\n")
    assert len(rows) == cards.BROWSE_PAGE_SIZE
    assert len(embed.description) < 2500                   # no wall of text (Discord allows 4096)
    assert all(len(r.split("\n")) <= 2 for r in rows)      # a title line and one short note
    assert embed.title == "📖 60 of 90 spots — 🍽️ food & drink"
    assert "Page 1/6" in embed.footer.text


async def test_pager_buttons_have_names_not_just_arrows():
    """#37: an emoji-only button reads as 'black left-pointing triangle'."""
    view = cards.BrowseView(_catalog(), label=None)
    assert [child.label for child in view.children] == ["Previous", "Next"]


async def test_rows_show_where_and_when():
    row = cards.build_browse_embed([_spot(1, area="123 Pine Ave, Long Beach, CA 90802",
                                         start_epoch=FRI_OCT_2)], 0).description
    assert "📍 Long Beach" in row and "📅 Fri Oct 2" in row


# ── /browse ─────────────────────────────────────────────────────────────────


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return SimpleNamespace(id=1)


def _interaction():
    async def defer(**kwargs):
        return None

    return SimpleNamespace(guild=SimpleNamespace(id=99), user=SimpleNamespace(id=7),
                           response=SimpleNamespace(defer=defer), followup=_Followup())


def _fetch(monkeypatch, events, calls=None):
    async def fake(gid):
        if calls is not None:
            calls.append(gid)
        if isinstance(events, Exception):
            raise events
        return [dict(e) for e in events]

    monkeypatch.setattr(bot, "_fetch_events", fake)
    bot._AREA_CACHE.clear()


def _choice(choices, value):
    return next(c for c in choices if c.value == value)


async def test_browse_with_every_filter(monkeypatch):
    _fetch(monkeypatch, _catalog())
    interaction = _interaction()
    await bot.browse_command.callback(
        interaction, category=_choice(bot._BROWSE_CHOICES, "food_drink"),
        area="Long Beach", when=_choice(bot._WHEN_CHOICES, "dated"))
    kwargs = interaction.followup.sent[0][1]
    expected = cards.filter_spots(_catalog(), category="food_drink", area="Long Beach", dated=True)
    assert kwargs["embed"].title == (
        f"📖 {len(expected)} of 60 spots — 🍽️ food & drink · 📍 Long Beach · 📅 has a date")


async def test_no_match_says_so_and_escapes_what_was_typed(monkeypatch):
    _fetch(monkeypatch, _catalog())
    interaction = _interaction()
    await bot.browse_command.callback(interaction, category=None, area="*Nowhere*", when=None)
    text = interaction.followup.sent[0][0][0]
    assert "Nothing matches" in text and "\\*Nowhere\\*" in text and "fewer filters" in text


async def test_an_empty_catalog_and_an_outage_read_differently(monkeypatch):
    _fetch(monkeypatch, [])
    interaction = _interaction()
    await bot.browse_command.callback(interaction, category=None, area=None, when=None)
    assert "catalog is empty" in interaction.followup.sent[0][0][0]

    _fetch(monkeypatch, RuntimeError("down"))
    interaction = _interaction()
    await bot.browse_command.callback(interaction, category=None, area=None, when=None)
    assert "Couldn't reach" in interaction.followup.sent[0][0][0]


async def test_area_autocomplete_fetches_once_per_minute(monkeypatch):
    calls = []
    _fetch(monkeypatch, _catalog(), calls)
    interaction = _interaction()
    first = await bot._browse_area_autocomplete(interaction, "lo")
    second = await bot._browse_area_autocomplete(interaction, "long")
    assert calls == ["99"]                                  # one fetch for two keystrokes
    assert [c.name for c in second] == ["Long Beach"]
    assert {c.name for c in first} >= {"Long Beach", "Los Angeles"}
    assert all(isinstance(c, app_commands.Choice) for c in first)


async def test_area_autocomplete_is_empty_when_the_catalog_is_down(monkeypatch):
    _fetch(monkeypatch, RuntimeError("down"))
    assert await bot._browse_area_autocomplete(_interaction(), "lo") == []
