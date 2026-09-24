"""Time-to-card: paste → card posted, as the person pasting sees it (Track C #18).

The bot times each paste and reports it here; the capture log (#23) times the
server's side of the same work. One JSON line per paste in
data/time_to_card.jsonl: when, how long, how many links, and whether a card
came out. No server id, user or link is stored, so there's nothing in it to
delete for a server (#21) and it can sit on the cross-server /dash.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from src.ingestion.serving.capture_stats import percentile, read_rows

OUTCOMES = ("card", "no_card", "error")
MAX_SECONDS = 3600.0          # the bot gives up long before this; anything bigger is junk


class TimingError(ValueError):
    """A report the bot never sends; the API turns it into a 400."""


class TimeToCardLog:
    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def record(self, seconds: float, outcome: str, links: int = 1) -> dict:
        try:
            seconds = float(seconds)
            links = int(links)
        except (TypeError, ValueError):
            raise TimingError("seconds and links must be numbers")
        if not 0 <= seconds <= MAX_SECONDS:
            raise TimingError(f"seconds must be between 0 and {int(MAX_SECONDS)}")
        if outcome not in OUTCOMES:
            raise TimingError(f"outcome must be one of {', '.join(OUTCOMES)}")
        if not 1 <= links <= 10:
            raise TimingError("links must be between 1 and 10")
        row = {"ts": int(time.time()), "seconds": round(seconds, 2), "outcome": outcome, "links": links}
        if self.path is not None:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(row) + "\n")
        return row

    def summary(self, now: Optional[float] = None, window_s: float = 86400.0) -> Optional[dict]:
        """Median and p95 of pastes that became cards — the last 24 h and all time —
        plus how many didn't. None when timing isn't logged at all."""
        if self.path is None:
            return None
        now = time.time() if now is None else now
        rows = read_rows(self.path, max_bytes=2_000_000)

        def stats(selected: list[dict]) -> dict:
            cards = [float(r["seconds"]) for r in selected if r.get("outcome") == "card"]
            return {
                "pastes": len(selected),
                "cards": len(cards),
                "median_s": round(percentile(cards, 50), 1) if cards else None,
                "p95_s": round(percentile(cards, 95), 1) if cards else None,
            }

        recent = [r for r in rows if now - float(r.get("ts", 0)) <= window_s]
        return {"last_24h": stats(recent), "all": stats(rows)}
