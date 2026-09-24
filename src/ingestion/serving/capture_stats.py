"""Capture timing statistics from the job queue's capture log (features #23, #29).

The queue appends one JSON line per finished job (`JobQueue.log_path`, by default
data/capture_jobs.jsonl). This turns those lines into counts and timings — the
same numbers for the `summarize_captures.py` command and the /dash operator view.

Only counts and seconds come out. The log's url_keys are hashes, used here to count
repeats and never returned, so nothing that identifies a post leaves this module.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Optional

# The per-URL budget (IngestionSettings.capture_budget_s) and the point where a
# Discord user has usually given up.
THRESHOLDS_S = (180.0, 300.0)


def read_rows(path: Path, max_bytes: Optional[int] = None) -> list[dict]:
    """Every well-formed line; a truncated line is skipped, not fatal.

    With `max_bytes`, only the last that-many bytes are read (the log only grows,
    and /dash reloads every few seconds); the first, probably partial, line of that
    window is dropped.
    """
    path = Path(path)
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as fh:
        if max_bytes is not None and size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()                       # finish the line we landed inside
        data = fh.read()
    rows = []
    for line in data.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile: the value at rank ceil(pct/100 × n). No numpy.

    The first version rounded (pct/100 × n + 0.5), and Python rounds halves to
    even, so whenever pct/100 × n was a whole number it came out one rank high —
    p95 of 20 values was the maximum, the median of 10 was the 6th. Found by the
    #18 tests, 2026-09-24.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(pct * len(ordered) / 100.0 - 1e-9)))
    return ordered[rank - 1]


def summarize(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    durations = [float(r.get("duration_s", 0.0)) for r in rows]
    states: dict[str, int] = {}
    stages: dict[str, list[float]] = {}
    seen: dict[str, int] = {}
    for row in rows:
        states[row.get("state", "unknown")] = states.get(row.get("state", "unknown"), 0) + 1
        for stage, secs in (row.get("stages") or {}).items():
            stages.setdefault(stage, []).append(float(secs))
        for key in row.get("url_keys") or []:
            seen[key] = seen.get(key, 0) + 1

    repeated = {k: n for k, n in seen.items() if n > 1}
    return {
        "captures": len(rows),
        "states": states,
        "duration_s": {
            "median": round(percentile(durations, 50), 1),
            "p90": round(percentile(durations, 90), 1),
            "p95": round(percentile(durations, 95), 1),
            "max": round(max(durations), 1) if durations else 0.0,
        },
        "over_threshold": {
            f"{int(t)}s": sum(1 for d in durations if d > t) for t in THRESHOLDS_S
        },
        "stage_median_s": {
            stage: round(percentile(secs, 50), 1) for stage, secs in sorted(stages.items())
        },
        "duplicates": {
            "distinct_links": len(seen),
            "links_captured_more_than_once": len(repeated),
            "wasted_captures": sum(n - 1 for n in repeated.values()),
        },
    }
