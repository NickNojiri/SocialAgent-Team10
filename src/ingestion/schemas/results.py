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

    @property
    def succeeded(self) -> bool:
        return self.record is not None
