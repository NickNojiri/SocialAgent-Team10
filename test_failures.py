"""Failure classes for the bot's plain-language messages (Track C #19)."""

import re

import pytest

from src.ingestion.schemas.results import FetchStatus, IngestionResult
from src.ingestion.serving.failures import CLASSES, failure_class, failures
from src.ingestion.serving.jobs import _merge_results

URL = "https://www.instagram.com/reel/A/"


def _result(status, reason=None, error=None):
    return IngestionResult(url=URL, fetch_status=status, rejection_reason=reason, fetch_error=error)


@pytest.mark.parametrize("status, reason, error, expected", [
    (FetchStatus.LOGIN_WALL, None, None, "private"),
    (FetchStatus.CONSENT_WALL, None, None, "private"),
    (FetchStatus.NOT_FOUND, None, None, "removed"),
    (FetchStatus.TIMEOUT, None, None, "timeout"),
    (FetchStatus.ERROR, None, "Page.goto: net::ERR_CONNECTION_RESET at https://x", "network"),
    (FetchStatus.ERROR, None, "net::ERR_CERT_AUTHORITY_INVALID", "network"),
    (FetchStatus.ERROR, None, None, "network"),
    (FetchStatus.ERROR, None, "unsupported URL (public http(s) only): 'ftp://x'", "unsupported"),
    (FetchStatus.ERROR, None, "Target page, context or browser has been closed", "bug"),
    (FetchStatus.ERROR, "capture exceeded the 180s budget", None, "too_slow"),
    (FetchStatus.ERROR, "unexpected error: KeyError: 'x'", None, "bug"),
    (FetchStatus.OK, "no human-readable text was extracted from the page", None, "no_text"),
    (FetchStatus.OK, "post is not clearly about a place, event, or food", None, "not_a_place"),
    (FetchStatus.OK, "event already passed: 2026-01-01T20:00:00+00:00", None, "past_event"),
    (FetchStatus.OK, "venue_name: Field required", None, "no_venue"),
])
def test_every_failure_gets_a_class(status, reason, error, expected):
    assert failure_class(_result(status, reason, error)) == expected
    assert expected in CLASSES


def test_only_failed_links_are_listed():
    ok = IngestionResult.model_construct(url="https://ok.test/", fetch_status=FetchStatus.OK, record=object())
    assert failures([ok, _result(FetchStatus.TIMEOUT)]) == [{"url": URL, "class": "timeout"}]


def test_the_bot_has_words_for_every_class():
    """app/cards.py mirrors these classes (the bot image can't import src/), so a
    class added here without a message there would reach users as a generic line."""
    from pathlib import Path

    source = (Path(__file__).parent / "app" / "cards.py").read_text(encoding="utf-8")
    for table in ("FAILURE_MESSAGES", "FAILURE_SHORT"):
        block = source.split(f"{table} = {{", 1)[1].split("\n}", 1)[0]
        assert set(re.findall(r'"([a-z_]+)":', block)) == set(CLASSES), table


def test_a_retry_replaces_the_first_failure_of_the_links_it_retried():
    first = {"events": [], "failures": [{"url": URL, "class": "timeout"},
                                        {"url": "https://b.test/", "class": "private"}]}
    retry = {"events": [{"id": "e1"}], "failures": []}               # the timeout healed
    merged = _merge_results(first, retry, [URL])
    assert merged["failures"] == [{"url": "https://b.test/", "class": "private"}]
