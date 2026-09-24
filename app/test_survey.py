"""Offline tests for /feedback and /survey (Track C #20).

Done-when: a SUS score can be collected from a participant without leaving
Discord. So: ten taps send ten answers (with Back to fix a slip), the request
carries this server's token, a failed save loses nothing, and feedback goes out
as typed.
"""

from types import SimpleNamespace

import httpx
import pytest

import bot
import cards
import feedback
from tenant_auth import tenant_headers

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SPOTBOT_SIGNING_KEY", "0" * 64)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, reply, down=False):
        self.reply, self.down, self.calls = reply, down, []

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        if self.down:
            raise httpx.ConnectError("down")
        return _Resp(self.reply)


class _Response:
    def __init__(self):
        self.edits, self.sent, self.modals = [], [], []

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    async def send_modal(self, modal):
        self.modals.append(modal)


def _interaction(guild=True):
    async def original_response():
        raise RuntimeError("not in tests")

    return SimpleNamespace(guild=SimpleNamespace(id=99) if guild else None, user=SimpleNamespace(id=7),
                           response=_Response(), original_response=original_response)


async def test_ten_taps_send_the_survey_with_this_servers_token(monkeypatch):
    fake = _FakeHttp({"score": 80.0})
    monkeypatch.setattr(feedback.httpx, "AsyncClient", fake)
    view = feedback.SurveyView("99", "P3")
    taps = [4, 2, 4, 1, 4, 2, 5, 2, 4, 2]
    for step, value in enumerate(taps):
        interaction = _interaction()
        await view.answer(interaction, value)
        if step < 9:
            assert interaction.response.edits[0]["embed"].title == f"Statement {step + 2} of 10"
    url, body, headers = fake.calls[0]
    assert url == f"{cards.ADMIN_URL}/api/survey"
    assert body == {"guild_id": "99", "answers": taps, "participant": "P3"}
    assert headers == tenant_headers("99")
    done = interaction.response.edits[0]
    assert done["embed"].title == "✅ Thank you!" and done["view"] is None
    assert "80" not in done["embed"].description            # no score shown to the participant
    assert view.is_finished()


async def test_back_fixes_a_slip(monkeypatch):
    view = feedback.SurveyView("99")
    assert view.back.disabled
    await view.answer(_interaction(), 5)
    await view.answer(_interaction(), 5)                    # meant 1
    interaction = _interaction()
    await view._go_back(interaction)
    assert view.answers == [5]
    assert interaction.response.edits[0]["embed"].title == "Statement 2 of 10"
    await view._go_back(_interaction())
    assert view.answers == [] and view.back.disabled


async def test_a_failed_save_keeps_every_answer_but_the_last(monkeypatch):
    monkeypatch.setattr(feedback.httpx, "AsyncClient", _FakeHttp({}, down=True))
    view = feedback.SurveyView("99")
    for _ in range(9):
        await view.answer(_interaction(), 3)
    interaction = _interaction()
    await view.answer(interaction, 3)
    assert len(view.answers) == 9 and not view.is_finished()
    assert "tap your last answer again" in interaction.response.edits[0]["content"]


async def test_each_screen_shows_one_statement_and_the_scale():
    embed = feedback.survey_embed(1, [5])
    assert feedback.SUS_STATEMENTS[1] in embed.description and "strongly agree" in embed.description
    assert len(feedback.SUS_STATEMENTS) == 10
    assert all("SpotBot" in s for s in feedback.SUS_STATEMENTS)


async def test_feedback_goes_out_as_typed(monkeypatch):
    fake = _FakeHttp({"saved": True})
    monkeypatch.setattr(feedback.httpx, "AsyncClient", fake)
    modal = feedback.FeedbackModal("99", "bug")
    modal.text._value = "Retry spun forever\non a TikTok link"
    interaction = _interaction()
    await modal.on_submit(interaction)
    assert fake.calls[0][1] == {"guild_id": "99", "kind": "bug",
                                "text": "Retry spun forever\non a TikTok link"}
    assert "without your name" in interaction.response.sent[0][0][0]


async def test_feedback_outage_says_to_try_again(monkeypatch):
    monkeypatch.setattr(feedback.httpx, "AsyncClient", _FakeHttp({}, down=True))
    modal = feedback.FeedbackModal("99", "idea")
    modal.text._value = "a map view"
    interaction = _interaction()
    await modal.on_submit(interaction)
    assert "try again" in interaction.response.sent[0][0][0]


async def test_the_commands_open_the_right_thing():
    interaction = _interaction()
    await bot.feedback_command.callback(interaction, "idea")
    assert isinstance(interaction.response.modals[0], feedback.FeedbackModal)
    assert interaction.response.modals[0].title == "Share an idea"

    interaction = _interaction(guild=False)                  # works in DMs too
    await bot.survey_command.callback(interaction, " P3 ")
    view = interaction.response.sent[0][1]["view"]
    assert (view.guild_id, view.participant) == ("dm-7", "P3")


@pytest.mark.parametrize("code", ["P 3", "Ｐ３", "x" * 17])
async def test_a_bad_participant_code_is_caught_before_the_survey(code):
    interaction = _interaction()
    await bot.survey_command.callback(interaction, code)
    args, kwargs = interaction.response.sent[0]
    assert "participant code" in args[0] and "view" not in kwargs
