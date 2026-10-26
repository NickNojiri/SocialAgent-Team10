"""Outcome envelopes: every URL produces exactly one IngestionResult, success or not."""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from src.ingestion.schemas.inspiration import EventInspiration


class FetchStatus(str, Enum):
    OK = "ok"
    # Walls are classified and reported, never bypassed — a blocked URL is a
    # valid, honest result.
    LOGIN_WALL = "login_wall"
    CONSENT_WALL = "consent_wall"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    ERROR = "error"


class PostGist(BaseModel):
    """What a reel that read fine but didn't make a spot is about (summarizer.describe_post)."""
    title: str
    kind: str                  # one of summarizer.POST_KINDS
    summary: str
    author: Optional[str] = None
    image_url: Optional[str] = None


class IngestionResult(BaseModel):
    url: str
    fetch_status: FetchStatus
    record: Optional[EventInspiration] = None
    rejection_reason: Optional[str] = None  # set when fetch was OK but validation failed
    # The fetcher's own error text for a non-OK fetch (e.g. "net::ERR_CONNECTION_RESET").
    # FetchStatus.ERROR covers both a dropped connection and a URL refused on policy;
    # this is what tells them apart for the job queue's retry policy (feature #26).
    fetch_error: Optional[str] = None
    raw_ref: Optional[Path] = None          # saved page snapshot for debugging failures
    # Which capture rung served this URL: "authed" | "browser" | "embed" | "none"
    # (pipeline/orchestrator.py::_capture). Lets the capture harness and /dash measure
    # the fallback chain instead of guessing.
    capture_path: Optional[str] = None
    # For a reel that read fine but didn't make a spot (a skit, a recipe, a meme): a title, a
    # kind and a summary of the whole video, so the bot can say what it was about.
    gist: Optional[PostGist] = None

    @property
    def succeeded(self) -> bool:
        return self.record is not None
