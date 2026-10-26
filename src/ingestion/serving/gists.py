"""The server's "not a place" list: reels that read fine but aren't somewhere to go (a recipe, a
meme, a story), each kept as a short gist (title, kind, summary) so people can tell what it's about
without watching it.

One JSON file per server under `data/gists/`, like the /setup settings, so deleting a server's
data (#21) is deleting one file. The legacy "" catalog keeps its list in `_shared.json`.
A gist that was moved off the spot list with the "Not a place" button also keeps that spot
(metadata, text and embedding), so "Move it back" restores it exactly.

Entries older than EXPIRY_DAYS are dropped when the list is read, unless the server turned that
off in /setup (`gists_expire`, on by default). At most MAX_PER_SERVER are kept, newest first.
Neither applies to a moved spot: its entry is the only copy left, so it stays until moved back.
No user ids are stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from src.ingestion.serving.guild_settings import _SAFE_GUILD

EXPIRY_DAYS = 30
MAX_PER_SERVER = 300
_FIELDS = ("title", "kind", "summary", "author", "image_url")


class GistError(ValueError):
    """A request the API turns into a 400."""


def gist_id(url: str) -> str:
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:12]


def _capped(rows: list[dict]) -> list[dict]:
    """Newest first, at most MAX_PER_SERVER plain gists; moved spots are always kept."""
    out, plain = [], 0
    for r in rows:
        if "spot" in r:
            out.append(r)
        elif plain < MAX_PER_SERVER:
            out.append(r)
            plain += 1
    return out


class GistStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._lock = threading.Lock()

    def path_for(self, guild_id: str) -> Path:
        gid = str(guild_id or "")
        if not gid:
            return self.root / "_shared.json"
        if not _SAFE_GUILD.match(gid):
            raise GistError("gists are per server; that server id isn't valid")
        return self.root / f"{gid}.json"

    def _read(self, guild_id: str) -> list[dict]:
        try:
            rows = json.loads(self.path_for(guild_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            return []           # a damaged file reads as an empty list, never a crash
        return [r for r in rows if isinstance(r, dict) and r.get("id")] if isinstance(rows, list) else []

    def _write(self, guild_id: str, rows: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".gists-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(rows, fh, ensure_ascii=False)
            os.replace(tmp, self.path_for(guild_id))   # a crash mid-write never leaves half a file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def add(self, guild_id: str, url: str, gist: dict, *, spot: dict | None = None,
            key: str | None = None) -> dict:
        """Save (or refresh) one reel's gist. The same reel twice is one entry.

        `key` (default: the url) names the entry. A moved spot passes its own id, because
        several spots can share one url (every manual spot is "manual://discord"). Refreshing
        an entry never drops a spot it holds: that copy is the only one left."""
        entry = {"id": gist_id(key or url), "url": str(url), "saved_at": int(time.time()),
                 **{k: gist.get(k) for k in _FIELDS}}
        if spot is not None:
            entry["spot"] = spot
        with self._lock:
            rows = self._read(guild_id)
            old = next((r for r in rows if r["id"] == entry["id"]), None)
            if old is not None and "spot" in old and "spot" not in entry:
                entry["spot"] = old["spot"]
            rest = [r for r in rows if r["id"] != entry["id"]]
            self._write(guild_id, _capped([entry] + rest))
        return entry

    def get(self, guild_id: str, gid: str) -> dict | None:
        """One gist whole (with its stored spot, if any), left in place."""
        with self._lock:
            return next((r for r in self._read(guild_id) if r["id"] == gid), None)

    def list(self, guild_id: str, *, expire: bool = True, now: float | None = None) -> list[dict]:
        """Newest first, without the stored spot itself ("moved" says whether there is one)."""
        cutoff = (now or time.time()) - EXPIRY_DAYS * 86400
        with self._lock:
            rows = self._read(guild_id)
            if expire:
                # A moved spot never expires: its entry is the spot's only copy.
                kept = [r for r in rows if "spot" in r or r.get("saved_at", 0) >= cutoff]
                if len(kept) != len(rows):
                    self._write(guild_id, kept)
                rows = kept
        return [{**{k: v for k, v in r.items() if k != "spot"}, "moved": "spot" in r} for r in rows]

    def pop(self, guild_id: str, gid: str) -> dict | None:
        """Remove one gist and return it whole (with its stored spot, if any)."""
        with self._lock:
            rows = self._read(guild_id)
            hit = next((r for r in rows if r["id"] == gid), None)
            if hit is not None:
                self._write(guild_id, [r for r in rows if r["id"] != gid])
        return hit

    def delete(self, guild_id: str) -> int:
        """Forget a server's whole list (#21). How many gists there were."""
        with self._lock:
            n = len(self._read(guild_id))
            try:
                self.path_for(guild_id).unlink()
            except FileNotFoundError:
                pass
        return n
