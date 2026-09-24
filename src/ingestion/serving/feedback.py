"""Feedback and SUS survey answers, one file per server (Track C #20).

`/feedback` sends a bug report or an idea; `/survey` walks a study participant
through the 10-statement System Usability Scale. Both are typed by the person on
purpose — nothing is scraped from chat — and both are stored without a Discord
user id or name: the survey keeps only the participant code the researcher hands
out ("P3"), so answers can be matched to session notes and nothing more.

One JSONL file per server under data/feedback/, so deleting a server's data
(#21) deletes it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from statistics import mean, stdev

MAX_TEXT = 1000
KINDS = ("bug", "idea")
_PARTICIPANT = re.compile(r"^[A-Za-z0-9_-]{0,16}$")
_SAFE_GUILD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # newlines and tabs are fine in a report


class FeedbackError(ValueError):
    """Input the bot never sends; the API turns it into a 400."""


def sus_score(answers: list[int]) -> float:
    """The standard SUS formula: odd statements score (answer - 1), even ones
    (5 - answer); the sum × 2.5 gives 0-100."""
    if len(answers) != 10 or any(not isinstance(a, int) or not 1 <= a <= 5 for a in answers):
        raise FeedbackError("a SUS response is 10 answers from 1 to 5")
    total = sum((a - 1) if i % 2 == 0 else (5 - a) for i, a in enumerate(answers))
    return total * 2.5


class FeedbackStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._lock = threading.Lock()

    def path_for(self, guild_id: str) -> Path:
        gid = str(guild_id or "")
        if not _SAFE_GUILD.match(gid):
            raise FeedbackError("feedback is per server; that server id isn't valid")
        return self.root / f"{gid}.jsonl"

    def _append(self, guild_id: str, row: dict) -> dict:
        path = self.path_for(guild_id)
        row = {"ts": int(time.time()), **row}
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def add_feedback(self, guild_id: str, kind: str, text: str) -> dict:
        if kind not in KINDS:
            raise FeedbackError(f"kind must be one of {', '.join(KINDS)}")
        text = str(text or "").strip()
        if not text:
            raise FeedbackError("feedback can't be empty")
        if len(text) > MAX_TEXT or _CONTROL.search(text):
            raise FeedbackError(f"feedback must be plain text under {MAX_TEXT} characters")
        return self._append(guild_id, {"type": "feedback", "kind": kind, "text": text})

    def add_sus(self, guild_id: str, answers: list[int], participant: str = "") -> dict:
        participant = str(participant or "").strip()
        if not _PARTICIPANT.match(participant):
            raise FeedbackError("participant code: up to 16 letters, digits, - or _")
        score = sus_score(list(answers))
        return self._append(guild_id, {"type": "sus", "participant": participant,
                                       "answers": list(answers), "score": score})

    def read(self, guild_id: str) -> list[dict]:
        path = self.path_for(guild_id)
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    def sus_summary(self, guild_id: str) -> dict:
        scores = [r["score"] for r in self.read(guild_id) if r.get("type") == "sus"]
        return {
            "responses": len(scores),
            "mean": round(mean(scores), 1) if scores else None,
            "sd": round(stdev(scores), 1) if len(scores) > 1 else None,
            "scores": scores,
        }

    def delete(self, guild_id: str) -> int:
        """Forget a server's feedback and survey answers (#21). Returns how many rows."""
        path = self.path_for(guild_id)
        with self._lock:
            n = len(self.read(guild_id))
            path.unlink(missing_ok=True)
        return n
