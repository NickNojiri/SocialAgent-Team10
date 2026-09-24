"""Per-server settings written by the /setup wizard (Track C #8).

One small JSON file per server under `data/guild_settings/`, so a server's
settings live apart from every other server's, and deleting that server's data
(#21) is deleting one file. Nothing here is global: an unconfigured server just
gets the defaults, which mean "behave exactly as before /setup existed".

    drop_channel_id  the one channel to watch for reels; None = every channel
                     the bot can read (the original behavior)
    home_city        the group's home city, as typed — distance on cards (#36)
                     and "near you" ranking read it later
    home_lat/lng     where that city is, looked up once when it's saved (#36);
                     None when the lookup failed or no city is set
    updated_at       unix seconds of the last save; 0 = never configured

No user ids, message text, or tokens are stored.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

DEFAULTS = {"drop_channel_id": None, "home_city": "", "home_lat": None, "home_lng": None,
            "updated_at": 0}
MAX_CITY_LEN = 80

# The characters collection_for_guild keeps. A guild id outside this set is
# refused rather than cleaned, so two ids can never share one settings file.
_SAFE_GUILD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CHANNEL_ID = re.compile(r"^[0-9]{1,20}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SettingsError(ValueError):
    """A value the wizard should never send; the API turns it into a 400."""


def clean_city(value: str) -> str:
    """Collapse whitespace; refuse control characters and anything too long."""
    city = " ".join(str(value).split())
    if _CONTROL.search(city):
        raise SettingsError("home city has control characters")
    if len(city) > MAX_CITY_LEN:
        raise SettingsError(f"home city is longer than {MAX_CITY_LEN} characters")
    return city


def clean_coordinate(value, limit: float) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SettingsError("coordinates must be numbers")
    if not -limit <= number <= limit:
        raise SettingsError("coordinates out of range")
    return number


def clean_channel_id(value) -> str | None:
    if value is None or value == "":
        return None
    channel = str(value).strip()
    if not _CHANNEL_ID.match(channel):
        raise SettingsError("drop channel must be a Discord channel id")
    return channel


class GuildSettingsStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._lock = threading.Lock()

    def path_for(self, guild_id: str) -> Path:
        gid = str(guild_id or "")
        if not _SAFE_GUILD.match(gid):
            raise SettingsError("settings are per server; that server id isn't valid")
        return self.root / f"{gid}.json"

    def get(self, guild_id: str) -> dict:
        path = self.path_for(guild_id)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError):
            saved = {}          # a damaged file reads as "not configured", never a crash
        return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}

    def update(self, guild_id: str, changes: dict) -> dict:
        """Change only the fields given; everything else is kept as it was.

        That is what makes re-running /setup safe: settings the wizard didn't
        touch survive, and the catalog itself is never touched at all.
        """
        clean: dict = {}
        if "home_city" in changes:
            clean["home_city"] = clean_city(changes["home_city"] or "")
        if "drop_channel_id" in changes:
            clean["drop_channel_id"] = clean_channel_id(changes["drop_channel_id"])
        if "home_lat" in changes or "home_lng" in changes:
            clean["home_lat"] = clean_coordinate(changes.get("home_lat"), 90.0)
            clean["home_lng"] = clean_coordinate(changes.get("home_lng"), 180.0)
        path = self.path_for(guild_id)
        with self._lock:
            merged = {**self.get(guild_id), **clean, "updated_at": int(time.time())}
            self.root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".settings-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(merged, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, path)       # a crash mid-write never leaves half a file
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return merged

    def delete(self, guild_id: str) -> bool:
        """Forget a server's settings (#21). True if there was anything to forget."""
        path = self.path_for(guild_id)
        with self._lock:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False
