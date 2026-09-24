"""Offline tests for capture failure messages (Track C #19).

Done-when: no user-visible failure is a bare error or a silent nothing. So every
failure class has a sentence that says what to do next, raw exception text never
reaches the channel, and a paste where only some links worked says which didn't.
"""

from types import SimpleNamespace

import pytest

import bot
import cards

pytestmark = pytest.mark.asyncio

URL_A = "https://www.instagram.com/reel/A/"
URL_B = "https://www.instagram.com/reel/B/"
NEXT_STEP = ("Retry", "add the spot yourself", "add it yourself", "Check the link", "paste that post",
             "/feedback", "in a minute")


@pytest.mark.parametrize("cls", list(cards.FAILURE_MESSAGES))
async def test_every_class_says_what_to_do_next(cls):
    text = cards.failure_message({"failures": [{"url": URL_A, "class": cls}]}, single=True)
    assert "{" not in text                                    # every placeholder filled
    assert any(step.lower() in text.lower() for step in NEXT_STEP), text


async def test_retry_is_only_mentioned_where_the_button_is():
    data = {"failures": [{"url": URL_A, "class": "timeout"}]}
    assert "Tap Retry" in cards.failure_message(data, single=True)
    after_retry = cards.failure_message(data, single=True, can_retry=False)
    assert "Retry" not in after_retry and "Paste it again later" in after_retry


async def test_many_links_one_reason_reads_as_one_sentence():
    data = {"failures": [{"url": URL_A, "class": "private"}, {"url": URL_B, "class": "private"}]}
    text = cards.failure_message(data, single=False)
    assert text.startswith("🔒") and "paste it again later" in text and "Retry" not in text


async def test_many_links_different_reasons_are_listed():
    data = {"failures": [{"url": URL_A, "class": "private"}, {"url": URL_B, "class": "removed"}]}
    text = cards.failure_message(data, single=False)
    assert "None of those 2 links worked" in text
    assert "• instagram.com/reel/A/ — private or needs a login" in text
    assert "• instagram.com/reel/B/ — deleted, or a wrong link" in text


@pytest.mark.parametrize("data, says", [
    ({"unreadable": 1}, "couldn't read that reel"),
    ({"rejected": 1}, "couldn't find a venue"),
    ({"failures": [{"url": URL_A, "class": "something-new"}], "unreadable": 1}, "couldn't read that reel"),
])
async def test_an_older_or_unknown_answer_still_gets_a_sentence(data, says):
    assert says in cards.failure_message(data, single=True)


# ── the bot ─────────────────────────────────────────────────────────────────


def _paste(monkeypatch, capture):
    replies, edits = [], []

    class Status:
        async def edit(self, **kwargs):
            edits.append(kwargs)

    async def reply(*args, **kwargs):
        replies.append((args, kwargs))
        return Status()

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "_add_reaction", nothing)
    monkeypatch.setattr(bot, "_swap_reaction", nothing)
    monkeypatch.setattr(bot, "_maybe_first_card_tip", nothing)
    monkeypatch.setattr(cards, "home_for", nothing)
    monkeypatch.setattr(cards, "report_time_to_card", nothing)
    monkeypatch.setattr(cards, "capture_urls", capture)
    message = SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=7, display_name="nick"),
                              reply=reply)
    return message, replies, edits


async def test_a_job_failure_never_shows_the_raw_error(monkeypatch):
    async def broken(*args, **kwargs):
        raise cards.CaptureFailed("KeyError: 'video_versions' in extract_raw")

    message, _replies, edits = _paste(monkeypatch, broken)
    await bot.handle_reel_capture(message, [URL_A])
    shown = edits[-1]["content"]
    assert "KeyError" not in shown and "video_versions" not in shown
    assert "Tap Retry" in shown and "/feedback bug" in shown


async def test_an_unreachable_catalog_reads_as_plain_words(monkeypatch):
    async def down(*args, **kwargs):
        raise ConnectionError("[Errno 11001] getaddrinfo failed")

    message, _replies, edits = _paste(monkeypatch, down)
    await bot.handle_reel_capture(message, [URL_A])
    shown = edits[-1]["content"]
    assert "Errno" not in shown and "admin app" not in shown and "try again in a minute" in shown


async def test_a_single_failed_link_gets_its_classs_sentence(monkeypatch):
    async def private(*args, **kwargs):
        return {"events": [], "unreadable": 1, "failures": [{"url": URL_A, "class": "private"}]}

    message, _replies, edits = _paste(monkeypatch, private)
    await bot.handle_reel_capture(message, [URL_A])
    assert edits[-1]["content"].startswith("🔒 That reel is private")


async def test_when_some_links_work_the_others_are_named_not_dropped(monkeypatch):
    async def half(*args, **kwargs):
        return {"events": [{"id": "e1", "venue": "Casa Loma", "category": "food_drink"}],
                "failures": [{"url": URL_B, "class": "removed"}]}

    message, replies, _edits = _paste(monkeypatch, half)
    await bot.handle_reel_capture(message, [URL_A, URL_B])
    notes = [args[0] for args, _ in replies if args and "didn't make a spot" in args[0]]
    assert notes == ["⚠️ 1 of 2 links didn't make a spot:\n• instagram.com/reel/B/ — deleted, or a wrong link"
                     "\n-# Paste those again later, or add them yourself."]


async def test_retry_that_fails_again_is_plain_too(monkeypatch):
    sent = []

    async def followup_send(*args, **kwargs):
        sent.append(args[0] if args else kwargs)

    async def defer(**kwargs):
        return None

    async def broken(*args, **kwargs):
        raise cards.CaptureFailed("RuntimeError: Chroma NotFound")

    monkeypatch.setattr(cards, "capture_urls", broken)
    interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=7),
                                  response=SimpleNamespace(defer=defer),
                                  followup=SimpleNamespace(send=followup_send))
    await cards.RetryButton(URL_A).callback(interaction)
    assert "RuntimeError" not in sent[0] and "/feedback bug" in sent[0]
