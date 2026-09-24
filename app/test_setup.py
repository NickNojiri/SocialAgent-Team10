"""Offline tests for the /setup wizard (Track C #8) — no gateway, no network.

What must hold: settings go to the admin API with this server's token and only
the fields that changed; a server that never ran /setup (or can't reach the
admin app) keeps capturing everywhere; once a reels channel is picked, only that
channel (and its threads) captures; only Manage Server gets the wizard.
"""

from types import SimpleNamespace

import discord
import httpx
import pytest

import bot
import cards
import setup_wizard
from tenant_auth import tenant_headers

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("SPOTBOT_SIGNING_KEY", "0" * 64)
    setup_wizard._CACHE.clear()
    yield
    setup_wizard._CACHE.clear()


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttp:
    """Stand-in for httpx.AsyncClient that records GET/PUT and can be 'down'."""

    def __init__(self, settings=None, down=False):
        self.settings = settings or {"drop_channel_id": None, "home_city": "", "updated_at": 0}
        self.down, self.calls = down, []

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        if self.down:
            raise httpx.ConnectError("admin app down")
        return _Resp({"settings": self.settings})

    async def put(self, url, json=None, headers=None):
        self.calls.append(("PUT", url, json, headers))
        changes = {k: v for k, v in json.items() if k != "guild_id"}
        return _Resp({"settings": {**self.settings, **changes, "updated_at": 1}})


# ── transport and cache ─────────────────────────────────────────────────────


async def test_settings_are_fetched_with_this_servers_token(monkeypatch):
    fake = _FakeHttp()
    monkeypatch.setattr(setup_wizard.httpx, "AsyncClient", fake)
    await setup_wizard.settings_for("g1")
    method, url, params, headers = fake.calls[0]
    assert (method, url, params) == ("GET", f"{cards.ADMIN_URL}/api/settings", {"guild_id": "g1"})
    assert headers == tenant_headers("g1")


async def test_settings_are_cached_until_asked_fresh(monkeypatch):
    fake = _FakeHttp()
    monkeypatch.setattr(setup_wizard.httpx, "AsyncClient", fake)
    await setup_wizard.settings_for("g1")
    await setup_wizard.settings_for("g1")
    assert len(fake.calls) == 1                      # one lookup per paste burst, not per paste
    await setup_wizard.settings_for("g1", fresh=True)
    assert len(fake.calls) == 2


async def test_an_unreachable_admin_app_is_none_and_not_cached(monkeypatch):
    monkeypatch.setattr(setup_wizard.httpx, "AsyncClient", _FakeHttp(down=True))
    assert await setup_wizard.settings_for("g1") is None
    monkeypatch.setattr(setup_wizard.httpx, "AsyncClient", _FakeHttp({"drop_channel_id": "5"}))
    assert (await setup_wizard.settings_for("g1"))["drop_channel_id"] == "5"


async def test_save_sends_only_what_changed_and_refreshes_the_cache(monkeypatch):
    fake = _FakeHttp()
    monkeypatch.setattr(setup_wizard.httpx, "AsyncClient", fake)
    saved = await setup_wizard.save_settings("g1", {"home_city": "Long Beach, CA"})
    method, url, body, headers = fake.calls[0]
    assert (method, url) == ("PUT", f"{cards.ADMIN_URL}/api/settings")
    assert body == {"guild_id": "g1", "home_city": "Long Beach, CA"}
    assert headers == tenant_headers("g1")
    assert await setup_wizard.settings_for("g1") == saved    # served from cache
    assert len(fake.calls) == 1


# ── where capture happens ───────────────────────────────────────────────────


def _message(channel_id=1, parent_id=None, guild=True):
    return SimpleNamespace(
        author=SimpleNamespace(bot=False, display_name="nick", id=7),
        content="https://www.instagram.com/reel/DU3evm2Ewhn/",
        guild=SimpleNamespace(id=99) if guild else None,
        channel=SimpleNamespace(id=channel_id, parent_id=parent_id),
    )


def _settings(monkeypatch, value):
    async def fake(guild_id, *, fresh=False):
        return value

    monkeypatch.setattr(setup_wizard, "settings_for", fake)


@pytest.mark.parametrize("settings, message, expected", [
    ({"drop_channel_id": None}, _message(1), True),              # never set up
    (None, _message(1), True),                                   # admin app unreachable
    ({"drop_channel_id": "5"}, _message(5), True),               # the reels channel
    ({"drop_channel_id": "5"}, _message(8, parent_id=5), True),  # a thread inside it
    ({"drop_channel_id": "5"}, _message(1), False),              # anywhere else
    ({"drop_channel_id": "5"}, _message(1, guild=False), True),  # DMs always capture
])
async def test_captures_here(monkeypatch, settings, message, expected):
    _settings(monkeypatch, settings)
    assert await setup_wizard.captures_here(message) is expected


async def test_a_reels_channel_limits_capture_to_it(monkeypatch):
    calls = []

    async def fake_capture(message, urls):
        calls.append(message.channel.id)

    monkeypatch.setattr(bot, "handle_reel_capture", fake_capture)
    monkeypatch.setattr(bot, "MUTED_CHANNELS", set())
    _settings(monkeypatch, {"drop_channel_id": "5"})
    await bot.on_message(_message(1))
    await bot.on_message(_message(5))
    assert calls == [5]


# ── the wizard ──────────────────────────────────────────────────────────────


class _Response:
    def __init__(self):
        self.edits, self.modals, self.sent, self.deferred = [], [], [], []

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    async def defer(self, **kwargs):
        self.deferred.append(kwargs)


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1)


def _interaction(*, manage=True, guild=True, create=None):
    g = None
    if guild:
        g = SimpleNamespace(id=99, me=None, get_channel=lambda _id: None,
                            create_text_channel=create)
    return SimpleNamespace(
        user=SimpleNamespace(id=7, __str__=lambda self: "nick"),
        guild=g,
        channel_id=1,
        permissions=SimpleNamespace(manage_guild=manage),
        response=_Response(),
        followup=_Followup(),
    )


CURRENT = {"drop_channel_id": "5", "home_city": "Long Beach, CA", "updated_at": 1}


async def test_the_embed_marks_what_will_change():
    text = setup_wizard.wizard_embed(CURRENT, {"home_city": "Irvine"}).description
    assert "<#5>" in text and "<#5> ✏️" not in text          # channel unchanged
    assert "Irvine ✏️" in text


async def test_save_sends_only_the_changes_then_finishes(monkeypatch):
    sent = []

    async def fake_save(guild_id, changes):
        sent.append((guild_id, dict(changes)))
        return {**CURRENT, **changes}

    monkeypatch.setattr(setup_wizard, "save_settings", fake_save)
    wizard = setup_wizard.SetupWizard("99", CURRENT)
    wizard.pending["home_city"] = "Irvine"
    interaction = _interaction()
    await wizard.save.callback(interaction)

    assert sent == [("99", {"home_city": "Irvine"})]         # the channel isn't re-sent
    edit = interaction.response.edits[0]
    assert edit["embed"].title == "✅ SpotBot is set up" and edit["view"] is None
    assert wizard.is_finished()


async def test_a_failed_save_keeps_the_wizard_open(monkeypatch):
    async def down(guild_id, changes):
        raise httpx.ConnectError("admin app down")

    monkeypatch.setattr(setup_wizard, "save_settings", down)
    wizard = setup_wizard.SetupWizard("99", CURRENT)
    interaction = _interaction()
    await wizard.save.callback(interaction)
    assert "nothing was changed" in interaction.response.edits[0]["embed"].description
    assert not wizard.is_finished()


async def test_every_channel_clears_the_reels_channel():
    wizard = setup_wizard.SetupWizard("99", CURRENT)
    interaction = _interaction()
    await wizard.every_channel.callback(interaction)
    assert wizard.pending == {"drop_channel_id": None}
    assert "every channel I can read ✏️" in interaction.response.edits[0]["embed"].description


async def test_create_channel_without_permission_explains_what_to_do():
    async def forbidden(name, reason=None):
        raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

    wizard = setup_wizard.SetupWizard("99", CURRENT)
    interaction = _interaction(create=forbidden)
    await wizard.create_channel.callback(interaction)
    assert "drop_channel_id" not in wizard.pending
    assert "Manage Channels" in interaction.response.edits[0]["embed"].description


async def test_create_channel_picks_the_new_channel():
    async def create(name, reason=None):
        assert name == "spot-drops"
        return SimpleNamespace(id=321)

    wizard = setup_wizard.SetupWizard("99", {**CURRENT, "drop_channel_id": None})
    await wizard.create_channel.callback(_interaction(create=create))
    assert wizard.pending == {"drop_channel_id": "321"}


async def test_the_city_modal_tidies_the_text_into_pending():
    wizard = setup_wizard.SetupWizard("99", CURRENT)
    modal = setup_wizard.CityModal(wizard)
    assert modal.city.default == "Long Beach, CA"            # re-running starts from what's saved
    modal.city._value = "  Irvine,   CA "
    await modal.on_submit(_interaction())
    assert wizard.pending == {"home_city": "Irvine, CA"}


# ── /setup and the join welcome ─────────────────────────────────────────────


async def test_setup_for_a_member_shows_settings_but_no_wizard(monkeypatch):
    _settings(monkeypatch, CURRENT)
    interaction = _interaction(manage=False)
    await bot.setup_command.callback(interaction)
    sent = interaction.followup.sent[0]
    assert "view" not in sent and sent["ephemeral"] is True
    assert "Manage Server" in sent["embed"].footer.text
    assert any(f.value == "<#5>" for f in sent["embed"].fields)


async def test_setup_for_a_manager_opens_the_wizard(monkeypatch):
    _settings(monkeypatch, CURRENT)
    interaction = _interaction(manage=True)
    await bot.setup_command.callback(interaction)
    sent = interaction.followup.sent[0]
    assert isinstance(sent["view"], setup_wizard.SetupWizard)
    assert sent["ephemeral"] is True and sent["view"].current == CURRENT


async def test_setup_when_settings_cant_load_shows_no_wizard(monkeypatch):
    _settings(monkeypatch, None)
    interaction = _interaction(manage=True)
    await bot.setup_command.callback(interaction)
    sent = interaction.followup.sent[0]
    assert "view" not in sent
    assert "Couldn't load" in sent["embed"].fields[0].value


async def test_setup_in_a_dm_says_theres_nothing_to_set_up():
    interaction = _interaction(guild=False)
    await bot.setup_command.callback(interaction)
    (args, kwargs), = interaction.response.sent
    assert "DMs" in args[0] and kwargs["ephemeral"] is True


async def test_joining_a_server_posts_one_welcome():
    posted = []

    async def send(**kwargs):
        posted.append(kwargs)

    channel = SimpleNamespace(send=send,
                              permissions_for=lambda me: SimpleNamespace(send_messages=True))
    await bot.on_guild_join(SimpleNamespace(id=99, me=None, system_channel=channel))
    assert len(posted) == 1 and "/setup" in posted[0]["embed"].description

    await bot.on_guild_join(SimpleNamespace(id=99, me=None, system_channel=None))   # no crash
    assert len(posted) == 1
