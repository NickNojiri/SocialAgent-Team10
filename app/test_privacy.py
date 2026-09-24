"""Offline tests for /privacy and deleting a server's data (Track C #21).

What must hold: nothing is deleted unless the exact server name (or "delete" in
a DM) is typed; the request carries this server's token and repeats its id; a
refusal or an outage says nothing was deleted; only Manage Server gets the
button; and the bot forgets its own settings for that server too.
"""

from types import SimpleNamespace

import httpx
import pytest

import bot
import cards
import privacy
import setup_wizard
from tenant_auth import tenant_headers

pytestmark = pytest.mark.asyncio

GUILD = SimpleNamespace(id=99, name="Taco Tuesday Crew",
                        channels=[SimpleNamespace(id=1), SimpleNamespace(id=2)],
                        threads=[SimpleNamespace(id=3)])


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SPOTBOT_SIGNING_KEY", "0" * 64)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return self.reply


class _Response:
    def __init__(self):
        self.sent, self.modals = [], []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    async def send_modal(self, modal):
        self.modals.append(modal)


def _interaction(*, guild=GUILD, manage=True):
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=7),
        permissions=SimpleNamespace(manage_guild=manage),
        response=_Response(),
        original_response=_no_message,
    )


async def _no_message():
    raise RuntimeError("not in tests")


REPORT = {"catalog": {"spots": 4, "shared_posts_kept": 1}, "settings": True}


# ── transport ───────────────────────────────────────────────────────────────


async def test_forget_repeats_the_id_and_sends_this_servers_token(monkeypatch):
    fake = _FakeHttp(_Resp(REPORT))
    monkeypatch.setattr(privacy.httpx, "AsyncClient", fake)
    assert await privacy.forget_data("99") == REPORT
    url, body, headers = fake.calls[0]
    assert url == f"{cards.ADMIN_URL}/api/forget"
    assert body == {"guild_id": "99", "confirm": "99"}
    assert headers == tenant_headers("99")


async def test_a_running_capture_is_a_busy_refusal(monkeypatch):
    monkeypatch.setattr(privacy.httpx, "AsyncClient", _FakeHttp(_Resp({}, status=409)))
    with pytest.raises(privacy.ForgetBusy):
        await privacy.forget_data("99")


# ── the two-step confirmation ───────────────────────────────────────────────


def _modal(monkeypatch, *, result=REPORT, expected=GUILD.name):
    calls, cleaned = [], []

    async def fake_forget(guild_id):
        calls.append(guild_id)
        if isinstance(result, Exception):
            raise result
        return result

    async def on_deleted():
        cleaned.append(True)

    monkeypatch.setattr(privacy, "forget_data", fake_forget)
    return privacy.ConfirmDeleteModal("99", expected, on_deleted), calls, cleaned


@pytest.mark.parametrize("typed", ["", "taco tuesday crew", "Taco Tuesday", "yes"])
async def test_nothing_is_deleted_unless_the_exact_name_is_typed(monkeypatch, typed):
    modal, calls, cleaned = _modal(monkeypatch)
    modal.answer._value = typed
    interaction = _interaction()
    await modal.on_submit(interaction)
    assert calls == [] and cleaned == []
    assert "nothing was deleted" in interaction.response.sent[0][0][0]


async def test_the_exact_name_deletes_and_reports(monkeypatch):
    modal, calls, cleaned = _modal(monkeypatch)
    modal.answer._value = f"  {GUILD.name} "
    interaction = _interaction()
    await modal.on_submit(interaction)
    assert calls == ["99"] and cleaned == [True]
    embed = interaction.response.sent[0][1]["embed"]
    assert "can't be undone" in embed.title
    assert "4 spots" in embed.description and "another server" in embed.description


@pytest.mark.parametrize("error, says", [
    (privacy.ForgetBusy("busy"), "still being captured"),
    (httpx.ConnectError("down"), "Couldn't reach"),
])
async def test_a_refusal_or_outage_says_nothing_was_deleted(monkeypatch, error, says):
    modal, _calls, cleaned = _modal(monkeypatch, result=error)
    modal.answer._value = GUILD.name
    interaction = _interaction()
    await modal.on_submit(interaction)
    text = interaction.response.sent[0][0][0]
    assert says in text and "nothing was deleted" in text.lower()
    assert cleaned == []                               # local settings kept too


async def test_a_dm_confirms_with_the_word_delete(monkeypatch):
    modal, calls, _ = _modal(monkeypatch, expected=privacy.DM_CONFIRM_WORD)
    assert modal.title == "Delete your saved SpotBot data"
    modal.answer._value = "delete"
    await modal.on_submit(_interaction(guild=None))
    assert calls == ["99"]


# ── /privacy ────────────────────────────────────────────────────────────────


async def test_members_see_what_is_kept_but_no_delete_button():
    interaction = _interaction(manage=False)
    await bot.privacy_command.callback(interaction)
    _args, kwargs = interaction.response.sent[0]
    assert "view" not in kwargs and kwargs["ephemeral"] is True
    assert "Manage Server can delete" in kwargs["embed"].fields[-1].value


async def test_managers_get_the_button_bound_to_the_server_name(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "CONFIG_FILE", tmp_path / "channels.json")
    interaction = _interaction(manage=True)
    await bot.privacy_command.callback(interaction)
    view = interaction.response.sent[0][1]["view"]
    assert isinstance(view, privacy.PrivacyView)
    assert (view.guild_id, view.expected) == ("99", GUILD.name)
    await view.delete.callback(interaction)
    assert isinstance(interaction.response.modals[0], privacy.ConfirmDeleteModal)

    setup_wizard._CACHE["99"] = (0.0, {"drop_channel_id": "1"})
    await view.on_deleted()                            # what runs after a successful delete
    assert "99" not in setup_wizard._CACHE


async def test_in_a_dm_you_can_delete_your_own_stash():
    interaction = _interaction(guild=None, manage=False)
    await bot.privacy_command.callback(interaction)
    kwargs = interaction.response.sent[0][1]
    assert kwargs["view"].guild_id == "dm-7" and kwargs["view"].expected == "delete"
    assert "You, and whoever runs SpotBot." in kwargs["embed"].fields[3].value


async def test_the_explanation_names_every_place_data_goes():
    text = " ".join(f.value for f in privacy.privacy_embed(in_server=True, can_delete=True).fields)
    for claim in ("Votes", "/setup", "Capture history", "failed to load", "doesn't keep",
                  "OpenStreetMap", "home city is looked up", "/share", "/survey"):
        assert claim in text


async def test_the_bot_forgets_its_own_settings_for_that_server(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "CONFIG_FILE", tmp_path / "channels.json")
    monkeypatch.setattr(bot, "MUTED_CHANNELS", {1, 50})
    monkeypatch.setattr(bot, "SUGGESTION_CHANNELS", {3, 60})
    monkeypatch.setattr(bot, "TIPPED_GUILDS", {99, 100})

    bot._forget_local_config(GUILD)
    assert bot.MUTED_CHANNELS == {50} and bot.SUGGESTION_CHANNELS == {60}   # other servers kept
    assert bot.TIPPED_GUILDS == {100}
    assert (tmp_path / "channels.json").exists()
