"""RSVPs and "find a time" polls on a spot (ADR-0008, borrowed from Partiful).

Discord votes are "Want to go / Not for me", one per Discord user. Event pages reach
people outside Discord, so they use softer answers (Going / Maybe / Can't) under a
typed name, keyed by a random id the browser keeps. Both live in the spot's Chroma
metadata as JSON strings, like `voters`, so nothing new has to be stored or deleted
(/privacy's forget already removes the whole spot).

Pure functions only: the endpoints in admin.py load the metadata, call these, and
write the result back. That keeps the rules testable without Chroma or HTTP.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

RSVP_STATES = ("going", "maybe", "cant")
MAX_RSVPS = 200          # per spot: an invite link is public, so cap what it can write
MAX_SLOTS = 5
MAX_NAME = 40
_RID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class PlanError(ValueError):
    """Bad input from an RSVP or poll request; the endpoint turns it into a 400."""


def _load(meta: dict, key: str, default):
    try:
        value = json.loads(meta.get(key) or "null")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:MAX_NAME]
    if not name:
        raise PlanError("a name is required")
    return name


def _check_rid(rid: str) -> str:
    if not _RID.match(str(rid or "")):
        raise PlanError("rsvp_id must be 8-64 letters, digits, - or _")
    return rid


def apply_rsvp(meta: dict, rid: str, name: str, state: str) -> dict:
    """Set (or change) one person's answer. Returns the updated metadata."""
    if state not in RSVP_STATES:
        raise PlanError(f"state must be one of {', '.join(RSVP_STATES)}")
    rid, name = _check_rid(rid), _clean_name(name)
    rsvps = _load(meta, "rsvps", {})
    if rid not in rsvps and len(rsvps) >= MAX_RSVPS:
        raise PlanError("this spot has reached its RSVP limit")
    rsvps[rid] = {"name": name, "state": state}
    meta["rsvps"] = json.dumps(rsvps)
    return meta


def rsvp_summary(meta: dict) -> dict:
    """{"going": [names], "maybe": [...], "cant": [...]} for the API and pages."""
    out = {s: [] for s in RSVP_STATES}
    for entry in _load(meta, "rsvps", {}).values():
        if isinstance(entry, dict) and entry.get("state") in out:
            out[entry["state"]].append(str(entry.get("name", "")))
    return {s: sorted(names, key=str.lower) for s, names in out.items()}


def open_poll(meta: dict, slots: list[str]) -> dict:
    """Start a "find a time" poll with 2-5 ISO times. Replaces any earlier poll."""
    clean = []
    for slot in slots:
        try:
            when = datetime.fromisoformat(str(slot))
        except ValueError as exc:
            raise PlanError(f"not an ISO date-time: {slot!r}") from exc
        clean.append(when.isoformat(timespec="minutes"))
    clean = list(dict.fromkeys(clean))
    if not 2 <= len(clean) <= MAX_SLOTS:
        raise PlanError(f"a poll needs 2 to {MAX_SLOTS} different times")
    meta["poll"] = json.dumps({"slots": clean, "votes": {}})
    return meta


def vote_poll(meta: dict, rid: str, name: str, picks: list[int]) -> dict:
    """One person's picks (slot indexes); voting again replaces them."""
    poll = _load(meta, "poll", {})
    slots = poll.get("slots") or []
    if not slots:
        raise PlanError("this spot has no poll")
    rid, name = _check_rid(rid), _clean_name(name)
    chosen = sorted({int(i) for i in picks})
    if any(i < 0 or i >= len(slots) for i in chosen):
        raise PlanError("pick a time from the poll")
    votes = poll.get("votes") or {}
    if rid not in votes and len(votes) >= MAX_RSVPS:
        raise PlanError("this poll has reached its vote limit")
    votes[rid] = {"name": name, "picks": chosen}
    poll["votes"] = votes
    meta["poll"] = json.dumps(poll)
    return meta


def poll_summary(meta: dict) -> dict | None:
    """Each slot with who picked it, plus the leading slot; None without a poll."""
    poll = _load(meta, "poll", {})
    slots = poll.get("slots") or []
    if not slots:
        return None
    names = [[] for _ in slots]
    for entry in (poll.get("votes") or {}).values():
        for i in entry.get("picks", []):
            if 0 <= i < len(slots):
                names[i].append(str(entry.get("name", "")))
    counts = [len(n) for n in names]
    best = max(range(len(slots)), key=lambda i: (counts[i], -i)) if any(counts) else None
    return {"slots": [{"at": at, "count": c, "names": sorted(n, key=str.lower)}
                      for at, c, n in zip(slots, counts, names)],
            "leading": best}
