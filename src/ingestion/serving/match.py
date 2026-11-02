"""Match rooms: the mailbox two phones use to run private set intersection (psi.py).

The server only ever holds blinded curve points, never an item, so it can't tell what
either person has, nor what they share. A room belongs to one server (guild), lives
30 minutes, and moves through three one-time steps:

  create (A)  -> room id + A's seat key
  a      (A)  A1                       stage "waiting"
  b      (B)  A2 (same length as A1), B1   -> B's seat key   stage "joined"
  finish (A)  B2 (same length as B1)     stage "done"

Anyone holding the room id may read it; only the seat keys may write. Every point must
decode to a P-256 point. /api/forget drops a server's rooms.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

from src.ingestion.serving.psi import decode

MAX_ITEMS = 2000
TTL_S = 30 * 60
MAX_ROOMS_PER_GUILD = 50


class MatchError(ValueError):
    """A bad match request (safe to show the user); `status` is the HTTP code."""

    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


@dataclass
class Room:
    id: str
    guild: str
    key_a: str
    created: float
    name_a: str = ""
    name_b: str = ""
    key_b: str = ""
    a1: list = field(default_factory=list)
    a2: list = field(default_factory=list)
    b1: list = field(default_factory=list)
    b2: list = field(default_factory=list)

    @property
    def stage(self) -> str:
        if self.b2:
            return "done"
        if self.b1:
            return "joined"
        return "waiting" if self.a1 else "new"

    def view(self) -> dict:
        """What a phone holding the room id may read: names, stage, blinded points."""
        return {"room": self.id, "stage": self.stage, "name_a": self.name_a, "name_b": self.name_b,
                "a1": self.a1, "a2": self.a2, "b1": self.b1, "b2": self.b2,
                "expires_in_s": max(0, int(self.created + TTL_S - time.time()))}


def _points(points, want_len: int | None = None) -> list[str]:
    if not isinstance(points, list) or not points or len(points) > MAX_ITEMS:
        raise MatchError(f"send between 1 and {MAX_ITEMS} points")
    if want_len is not None and len(points) != want_len:
        raise MatchError("wrong number of points for this step")
    for p in points:
        try:
            decode(p if isinstance(p, str) else "")
        except ValueError:
            raise MatchError("every item must be a P-256 point") from None
    return list(points)


def _name(name) -> str:
    return " ".join(str(name or "").split())[:40]


class MatchRooms:
    def __init__(self):
        self._rooms: dict[str, Room] = {}
        self._lock = threading.Lock()

    def _sweep(self) -> None:
        now = time.time()
        for rid in [r.id for r in self._rooms.values() if now - r.created > TTL_S]:
            del self._rooms[rid]

    def _get(self, guild: str, rid: str) -> Room:
        room = self._rooms.get(rid)
        if room is None or room.guild != guild:
            raise MatchError("that match link has expired, or isn't for this server", 404)
        return room

    def create(self, guild: str, name: str = "") -> dict:
        with self._lock:
            self._sweep()
            if sum(1 for r in self._rooms.values() if r.guild == guild) >= MAX_ROOMS_PER_GUILD:
                raise MatchError("too many open matches on this server; try again later", 429)
            room = Room(id=secrets.token_urlsafe(18), guild=guild, key_a=secrets.token_urlsafe(18),
                        created=time.time(), name_a=_name(name))
            self._rooms[room.id] = room
            return {"room": room.id, "key": room.key_a}

    def get(self, guild: str, rid: str) -> dict:
        with self._lock:
            self._sweep()
            return self._get(guild, rid).view()

    def step_a(self, guild: str, rid: str, key: str, a1) -> dict:
        with self._lock:
            room = self._get(guild, rid)
            if not secrets.compare_digest(key or "", room.key_a) or room.a1:
                raise MatchError("not allowed", 403)
            room.a1 = _points(a1)
            return room.view()

    def step_b(self, guild: str, rid: str, a2, b1, name: str = "") -> dict:
        with self._lock:
            room = self._get(guild, rid)
            if not room.a1 or room.b1:
                raise MatchError("this match isn't waiting for a friend", 409)
            room.a2, room.b1 = _points(a2, len(room.a1)), _points(b1)
            room.name_b, room.key_b = _name(name), secrets.token_urlsafe(18)
            return {**room.view(), "key": room.key_b}

    def finish(self, guild: str, rid: str, key: str, b2) -> dict:
        with self._lock:
            room = self._get(guild, rid)
            if not secrets.compare_digest(key or "", room.key_a) or not room.b1 or room.b2:
                raise MatchError("not allowed", 403)
            room.b2 = _points(b2, len(room.b1))
            return room.view()

    def forget_guild(self, guild: str) -> int:
        with self._lock:
            ids = [r.id for r in self._rooms.values() if r.guild == guild]
            for rid in ids:
                del self._rooms[rid]
            return len(ids)
