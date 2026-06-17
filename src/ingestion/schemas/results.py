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
    raw_ref: Optional[Path] = None          # saved page snapshot for debugging failures

    @property
    def succeeded(self) -> bool:
        return self.record is not None
