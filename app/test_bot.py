"""Offline tests for the bot's message routing — no gateway, no network.

Imports the real bot module (safe: the client only connects under __main__)
and drives on_message with fake message objects, with capture/recommend
calls monkeypatched to recorders.
"""

from types import SimpleNamespace

import pytest

import bot

pytestmark = pytest.mark.asyncio


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
