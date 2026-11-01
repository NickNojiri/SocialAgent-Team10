"""Import backlog: reel links from a user's own Instagram data download (ADR-0008).

The phone reads the export file itself and sends only the links, never message text
or who sent them. This module holds those links per server and hands them to the
capture queue a few at a time, so a backlog of hundreds never crowds out a live paste
and never hammers Instagram's public reel pages.

Only canonical Instagram post links are accepted (`https://www.instagram.com/reel/<code>/`),
so nothing a client sends here can point the capture browser anywhere else.
"""
from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Optional

_CODE = re.compile(r"^https?://(?:www\.|m\.)?instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,40})/?(?:[?#].*)?$")
MAX_PER_REQUEST = 2000
MAX_PENDING_PER_GUILD = 5000


class ImportRequestError(ValueError):
    """A bad import request (message is safe to show the user)."""


def canonical(url: str) -> Optional[str]:
    """The canonical reel link for an Instagram post URL, or None if it isn't one."""
    m = _CODE.match((url or "").strip())
    return f"https://www.instagram.com/reel/{m.group(1)}/" if m else None


class ImportBacklog:
    """Pending import links per server, oldest first; optionally kept in a JSON file."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        # guild -> OrderedDict[url -> user_id] (insertion order = import order)
        self._pending: dict[str, OrderedDict[str, str]] = {}
        self._seen: dict[str, set[str]] = {}       # every link ever queued, so re-imports skip them
        self._sent: dict[str, int] = {}
        self._turn = 0
        self._load()

    # ── public ────────────────────────────────────────────────────────────

    def add(self, guild_id: str, user_id: str, urls: Iterable[str]) -> dict:
        urls = list(urls)
        if len(urls) > MAX_PER_REQUEST:
            raise ImportRequestError(f"too many links: send at most {MAX_PER_REQUEST} at a time")
        gid = str(guild_id or "")
        added = duplicates = rejected = 0
        with self._lock:
            pending = self._pending.setdefault(gid, OrderedDict())
            seen = self._seen.setdefault(gid, set())
            for raw in urls:
                url = canonical(raw)
                if url is None:
                    rejected += 1
                elif url in seen:
                    duplicates += 1
                elif len(pending) >= MAX_PENDING_PER_GUILD:
                    raise ImportRequestError(f"this server already has {len(pending)} links waiting")
                else:
                    pending[url] = str(user_id or "")
                    seen.add(url)
                    added += 1
            self._save()
            return {"added": added, "duplicates": duplicates, "rejected": rejected,
                    "pending": len(pending)}

    def take(self, n: int) -> tuple[str, str, list[str]] | None:
        """Up to `n` of one server's oldest links (servers take turns), or None.

        The batch is all from one user, because a capture job runs for one server
        and its links are credited to whoever imported them.
        """
        with self._lock:
            busy = [g for g, p in self._pending.items() if p]
            if not busy:
                return None
            gid = busy[self._turn % len(busy)]
            self._turn += 1
            pending = self._pending[gid]
            user = next(iter(pending.values()))
            batch = [u for u, who in pending.items() if who == user][: max(1, int(n))]
            for u in batch:
                del pending[u]
            self._sent[gid] = self._sent.get(gid, 0) + len(batch)
            self._save()
            return gid, user, batch

    def put_back(self, guild_id: str, user_id: str, urls: list[str]) -> None:
        """Return a batch the capture queue couldn't take, at the front of the line."""
        gid = str(guild_id or "")
        with self._lock:
            pending = self._pending.setdefault(gid, OrderedDict())
            for u in reversed(urls):
                pending[u] = user_id
                pending.move_to_end(u, last=False)
            self._sent[gid] = max(0, self._sent.get(gid, 0) - len(urls))
            self._save()

    def status(self, guild_id: str) -> dict:
        gid = str(guild_id or "")
        with self._lock:
            return {"pending": len(self._pending.get(gid, ())), "sent": self._sent.get(gid, 0)}

    def forget_guild(self, guild_id: str) -> int:
        """Drop everything kept for one server (/api/forget). Returns links dropped."""
        gid = str(guild_id or "")
        with self._lock:
            n = len(self._pending.pop(gid, ()))
            self._seen.pop(gid, None)
            self._sent.pop(gid, None)
            self._save()
            return n

    # ── storage ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for gid, g in (data.get("guilds") or {}).items():
            self._pending[gid] = OrderedDict((u, w) for u, w in g.get("pending", []))
            self._seen[gid] = set(g.get("seen", []))
            self._sent[gid] = int(g.get("sent", 0))

    def _save(self) -> None:
        if not self.path:
            return
        guilds = {
            gid: {"pending": list(self._pending.get(gid, {}).items()),
                  "seen": sorted(self._seen.get(gid, ())), "sent": self._sent.get(gid, 0)}
            for gid in set(self._pending) | set(self._seen)
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"guilds": guilds}), encoding="utf-8", newline="\n")
        tmp.replace(self.path)
