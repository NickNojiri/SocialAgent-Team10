"""Why a link didn't become a spot, as one short class name (Track C #19).

The bot turns each class into a plain sentence with a next step, so no failure
reaches a user as a bare error or as nothing at all. The classes come from what
the pipeline already reports — the fetch status, the fetcher's error text, and
the rejection reason — and are the ones #19 asks for (private reel, no video/text,
timeout, no venue, not a place) plus the few the pipeline can also produce.

PROVISIONAL until Track A hands over its final failure taxonomy (#1 → #19): when
it does, map its names onto these here, and nowhere else.
"""

from __future__ import annotations

from typing import Optional

from src.ingestion.schemas.results import FetchStatus, IngestionResult

CLASSES = (
    "private",       # login or consent wall: a private reel, or one that needs a login
    "removed",       # the post is gone, or the link is wrong
    "timeout",       # the page didn't load in time
    "network",       # a dropped connection or DNS hiccup on the way to the site
    "unsupported",   # not a link SpotBot can read (not http(s))
    "too_slow",      # the whole capture ran past its time budget
    "no_text",       # the page loaded, but there was no caption or text to read
    "not_a_place",   # read fine, but it isn't about a place, event or food
    "past_event",    # an event that has already happened
    "no_venue",      # read fine, but no place could be pinned down
    "bug",           # something failed on SpotBot's side
)


def failure_class(result: IngestionResult) -> Optional[str]:
    """The class for a failed result; None for a success."""
    if result.record is not None:
        return None
    reason = result.rejection_reason or ""
    status = result.fetch_status
    if status in (FetchStatus.LOGIN_WALL, FetchStatus.CONSENT_WALL):
        return "private"
    if status is FetchStatus.NOT_FOUND:
        return "removed"
    if status is FetchStatus.TIMEOUT:
        return "timeout"
    if status is FetchStatus.ERROR:
        if reason.startswith("capture exceeded"):
            return "too_slow"
        if reason.startswith("unexpected error"):
            return "bug"
        error = result.fetch_error or ""
        if error.startswith("unsupported URL"):
            return "unsupported"
        if not error or "net::ERR_" in error:
            return "network"            # couldn't reach the site, retryable or not
        return "bug"
    # The page was read; the post itself didn't make a spot.
    if reason.startswith("no human-readable text"):
        return "no_text"
    if reason.startswith("post is not clearly about"):
        return "not_a_place"
    if reason.startswith("event already passed"):
        return "past_event"
    return "no_venue"


def failures(results: list[IngestionResult]) -> list[dict]:
    """[{url, class}] for every link in a run that didn't become a spot."""
    return [{"url": r.url, "class": failure_class(r)} for r in results if r.record is None]
