"""Offline tests for the bot's message routing — no gateway, no network.

Imports the real bot module (safe: the client only connects under __main__)
and drives on_message with fake message objects, with capture/recommend
calls monkeypatched to recorders.
"""

from types import SimpleNamespace

import pytest

import bot
import setup_wizard

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _never_set_up(monkeypatch):
    """Routing tests run as a server that never ran /setup — and never touch the network."""
    async def defaults(guild_id, *, fresh=False):
        return {"drop_channel_id": None, "home_city": "", "updated_at": 0}

    monkeypatch.setattr(setup_wizard, "settings_for", defaults)


class FakeChannel:
    def __init__(self, channel_id=1):
        self.id = channel_id
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


def make_message(content="", guild=True, channel_id=1):
    return SimpleNamespace(
        author=SimpleNamespace(bot=False, display_name="nick"),
        content=content,
        guild=SimpleNamespace(id=99) if guild else None,
        channel=FakeChannel(channel_id),
    )


REEL = "https://www.instagram.com/reel/DU3evm2Ewhn/"


class TestOnMessageRouting:
    def _patch_capture(self, monkeypatch):
        calls = []

        async def fake_capture(message, urls):
            calls.append(urls)

        monkeypatch.setattr(bot, "handle_reel_capture", fake_capture)
        return calls

    async def test_link_anywhere_triggers_capture(self, monkeypatch):
        calls = self._patch_capture(monkeypatch)
        await bot.on_message(make_message(f"check this {REEL}"))
        assert calls == [[REEL]]

    async def test_tiktok_link_triggers_capture(self, monkeypatch):
        calls = self._patch_capture(monkeypatch)
        await bot.on_message(make_message("https://www.tiktok.com/@u/video/123456789"))
        assert len(calls) == 1 and "tiktok" in calls[0][0]

    async def test_muted_channel_is_skipped(self, monkeypatch):
        calls = self._patch_capture(monkeypatch)
        monkeypatch.setattr(bot, "MUTED_CHANNELS", {42})
        await bot.on_message(make_message(REEL, channel_id=42))
        assert calls == []

    async def test_bot_authors_are_ignored(self, monkeypatch):
        calls = self._patch_capture(monkeypatch)
        msg = make_message(REEL)
        msg.author.bot = True
        await bot.on_message(msg)
        assert calls == []

    async def test_dm_without_link_gets_welcome(self, monkeypatch):
        self._patch_capture(monkeypatch)
        msg = make_message("hey what do you do", guild=False)
        await bot.on_message(msg)
        assert len(msg.channel.sent) == 1
        _args, kwargs = msg.channel.sent[0]
        assert "Drop a reel" in kwargs["embed"].title

    async def test_plain_guild_chat_is_silent_without_suggestions(self, monkeypatch):
        calls = self._patch_capture(monkeypatch)
        monkeypatch.setattr(bot, "SUGGESTION_CHANNELS", set())
        msg = make_message("we should get tacos")
        await bot.on_message(msg)
        assert calls == []
        assert msg.channel.sent == []


class TestConfigRoundtrip:
    async def test_save_and_load_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "CONFIG_FILE", tmp_path / "channels.json")
        monkeypatch.setattr(bot, "MUTED_CHANNELS", {1, 2})
        monkeypatch.setattr(bot, "SUGGESTION_CHANNELS", {3})
        monkeypatch.setattr(bot, "TIPPED_GUILDS", {99})
        bot.save_config()

        monkeypatch.setattr(bot, "MUTED_CHANNELS", set())
        monkeypatch.setattr(bot, "SUGGESTION_CHANNELS", set())
        monkeypatch.setattr(bot, "TIPPED_GUILDS", set())
        bot.load_config()
        assert bot.MUTED_CHANNELS == {1, 2}
        assert bot.SUGGESTION_CHANNELS == {3}
        assert bot.TIPPED_GUILDS == {99}


    async def test_config_saves_into_a_folder_that_doesnt_exist_yet(self, tmp_path, monkeypatch):
        """Staging points BOT_CONFIG_FILE at a volume (#11); a fresh one starts empty."""
        target = tmp_path / "state" / "channels.json"
        monkeypatch.setattr(bot, "CONFIG_FILE", target)
        monkeypatch.setattr(bot, "MUTED_CHANNELS", {5})
        bot.save_config()
        assert '"muted": [\n    5\n  ]' in target.read_text(encoding="utf-8")


class TestMentionSafety:
    async def test_client_never_pings_from_content(self):
        """Venue names reach plain-content sends (/catalog, the went-there prompt,
        recommendation markdown), and the Add-manually / Edit modals accept any
        text — so a venue called "@everyone" would ping the server. The client-wide
        default blocks pings on every send, follow-ups included (THREAT_MODEL T1)."""
        am = bot.bot.allowed_mentions
        assert am is not None
        assert am.everyone is False
        assert am.users is False
        assert am.roles is False

    async def test_render_capture_shares_the_result_shape(self, monkeypatch):
        """Sync and async capture hand the same dict to one renderer."""
        msg = make_message("x")
        status = SimpleNamespace(edits=[])

        async def edit(**kwargs):
            status.edits.append(kwargs)

        status.edit = edit
        monkeypatch.setattr(bot, "_maybe_first_card_tip", lambda m: _noop())
        await bot._render_capture(msg, status, ["https://www.instagram.com/reel/A/"], {"events": []})
        assert status.edits and "couldn't find a venue" in status.edits[0]["content"]


async def _noop():
    return None
