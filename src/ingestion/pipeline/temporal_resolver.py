"""Resolve raw candidate_times into a validated UTC schedule (Phase 4).

Reuses src/logic/parser.py::TimeParser.parse_datetime (dateparser) as the parsing
surface; this module owns the fragment-combination, timezone, and expiry logic.

Two timestamps are kept separate:
  - relative base  = the post's provenance.fetched_at (when "this Friday" was said)
  - expiry ref     = execution `now` in UTC (whether the event has already passed)
Both are injectable so tests are deterministic. dateparser is offline, so this whole
layer runs with no network.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.inspiration import (
    EventInspiration,
    EventSchedule,
    ScheduleStatus,
)

log = logging.getLogger("ingestion.temporal")

# dateparser period values, coarsest-to-finest. 'time' means a time-of-day was found.
_DATE_PERIODS = {"day", "week", "month", "year"}
_DEFAULT_HOUR = 18  # date-only events default to 6pm local (time_known=False)


class TemporalResolver:
    def __init__(self, settings: IngestionSettings, now: Optional[datetime] = None):
        self.settings = settings
        # Lazy import keeps the --no-temporal path free of dateparser.
        from src.logic.parser import TimeParser

        self._parser = TimeParser()
        # Pinned execution reference for the whole run (injectable for tests).
        self._now = now or datetime.now(timezone.utc)
        self._tag = "timeparser/dateparser"

    def resolve(self, record: EventInspiration) -> tuple[EventInspiration, Optional[str]]:
        """Return (record_with_schedule, None) or (record, rejection_reason) when expired."""
        base = self._relative_base(record)
        start, time_known, parsed_from = self._best_start(record.candidate_times, base)

        if start is None:
            schedule = EventSchedule(status=ScheduleStatus.UNSCHEDULED)
            return self._attach(record, schedule), None

        grace = timedelta(minutes=self.settings.expiry_grace_minutes)
        if self.settings.reject_expired and start < self._now - grace:
            return record, f"event already passed: {start.isoformat()}"

        end = start + timedelta(hours=self.settings.default_duration_hours)
        schedule = EventSchedule(
            status=ScheduleStatus.SCHEDULED,
            start_utc=start,
            end_utc=end,
            time_known=time_known,
            parsed_from=parsed_from,
        )
        return self._attach(record, schedule), None

    # ── internals ────────────────────────────────────────────────────────────

    def _relative_base(self, record: EventInspiration) -> datetime:
        """Anchor relative expressions to capture time, expressed in the assumed tz
        (dateparser RELATIVE_BASE is naive and interpreted in TIMEZONE)."""
        fetched = record.provenance.fetched_at or self._now
        try:
            from zoneinfo import ZoneInfo

            local = fetched.astimezone(ZoneInfo(self.settings.assumed_tz))
        except Exception:
            local = fetched
        return local.replace(tzinfo=None)

    def _best_start(
        self, candidates: list[str], base: datetime
    ) -> tuple[Optional[datetime], bool, Optional[str]]:
        """Map fragments → a single best UTC start. Returns (start, time_known, parsed_from)."""
        timed: list[tuple[datetime, str]] = []  # had a time-of-day
        dated: list[tuple[datetime, str]] = []  # date granularity only
        full: list[tuple[datetime, str]] = []   # time-of-day AND a non-base date (e.g. ISO)
        base_date = base.date()

        for raw in candidates:
            dt, period = self._parse_one(raw, base)
            if dt is None:
                continue
            if period == "time":
                timed.append((dt, raw))
                if dt.astimezone(_local_tz(self.settings.assumed_tz)).date() != base_date:
                    full.append((dt, raw))
            elif period in _DATE_PERIODS:
                dated.append((dt, raw))

        # Priority 0: a candidate that already carries both date and time.
        if full:
            dt, raw = _earliest_future(full, self._now)
            return dt, True, raw

        # Priority 1: combine a date fragment with a time fragment ("Friday" + "8pm").
        if dated and timed:
            date_dt, date_raw = _earliest_future(dated, self._now)
            time_dt, time_raw = _earliest_future(timed, self._now)
            combined = _combine(date_dt, time_dt, self.settings.assumed_tz)
            return combined, True, f"{date_raw} + {time_raw}"

        # Priority 2: a time only (date = capture day).
        if timed:
            dt, raw = _earliest_future(timed, self._now)
            return dt, True, raw

        # Priority 3: a date only → default hour, time unknown.
        if dated:
            dt, raw = _earliest_future(dated, self._now)
            return _at_default_hour(dt, self.settings.assumed_tz), False, raw

        # Priority 4: nothing parsed.
        return None, False, None

    def _parse_one(self, raw: str, base: datetime) -> tuple[Optional[datetime], Optional[str]]:
        """Parse one fragment, retrying with casual prefixes stripped. dateparser
        returns None for 'this Friday' but resolves plain 'Friday' (PREFER future)."""
        dt, period = self._parser.parse_datetime(raw, relative_base=base, tz=self.settings.assumed_tz)
        if dt is None:
            cleaned = re.sub(r"(?i)^\s*this\s+", "", raw).strip()
            if cleaned and cleaned != raw:
                dt, period = self._parser.parse_datetime(
                    cleaned, relative_base=base, tz=self.settings.assumed_tz
                )
        return dt, period

    def _attach(self, record: EventInspiration, schedule: EventSchedule) -> EventInspiration:
        provenance = record.provenance.model_copy(update={"temporal_parser": self._tag})
        return record.model_copy(update={"schedule": schedule, "provenance": provenance})


# ── module helpers ───────────────────────────────────────────────────────────


def _local_tz(tz_name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _earliest_future(items: list[tuple[datetime, str]], now: datetime) -> tuple[datetime, str]:
    """Prefer the earliest start that is still in the future; else the latest past one."""
    future = sorted((it for it in items if it[0] >= now), key=lambda it: it[0])
    if future:
        return future[0]
    return sorted(items, key=lambda it: it[0])[-1]


def _combine(date_dt: datetime, time_dt: datetime, tz_name: str) -> datetime:
    """Take the calendar date from date_dt and the wall-clock time from time_dt
    (both UTC), composing in local tz so the intended local time is preserved."""
    tz = _local_tz(tz_name)
    d = date_dt.astimezone(tz)
    t = time_dt.astimezone(tz)
    local = d.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def _at_default_hour(date_dt: datetime, tz_name: str) -> datetime:
    tz = _local_tz(tz_name)
    local = date_dt.astimezone(tz).replace(
        hour=_DEFAULT_HOUR, minute=0, second=0, microsecond=0
    )
    return local.astimezone(timezone.utc)
