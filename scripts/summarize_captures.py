"""How slow is capture, and how often do we capture the same reel twice?

Reads the capture log the job queue appends to (one JSON line per finished
job, `data/capture_jobs.jsonl` by default) and prints the duration
distribution, how many captures ran past the budget, per-stage medians, and
duplicate captures.

    python scripts/summarize_captures.py
    python scripts/summarize_captures.py --log data/capture_jobs.jsonl --json

Feature #23. The log holds links and timings only — no tokens, no user ids,
no message text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

DEFAULT_LOG = Path("data/capture_jobs.jsonl")
# The two lines a reader actually cares about: the per-URL budget
# (IngestionSettings.capture_budget_s) and the point where Discord users give up.
THRESHOLDS_S = (180.0, 300.0)


def read_rows(path: Path) -> list[dict]:
    """Every well-formed line; a truncated last line is skipped, not fatal."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — no numpy, and exact on small samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(pct / 100.0 * len(ordered) + 0.5))))
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


def render(summary: dict) -> str:
    if not summary["captures"]:
        return "No captures logged yet — run some captures with INGEST_ASYNC=1 first."
    d, dup = summary["duration_s"], summary["duplicates"]
    lines = [
        f"captures            {summary['captures']}  ({', '.join(f'{k}={v}' for k, v in sorted(summary['states'].items()))})",
        f"duration (s)        median {d['median']}  p90 {d['p90']}  p95 {d['p95']}  max {d['max']}",
        "over budget         " + "  ".join(
            f"{n} past {t}" for t, n in summary["over_threshold"].items()
        ),
        "stage medians (s)   " + ("  ".join(
            f"{k} {v}" for k, v in summary["stage_median_s"].items()
        ) or "none recorded"),
        f"duplicates          {dup['links_captured_more_than_once']} of {dup['distinct_links']} links "
        f"captured more than once ({dup['wasted_captures']} wasted capture(s))",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help=f"capture log (default {DEFAULT_LOG})")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()

    summary = summarize(read_rows(args.log))
    print(json.dumps(summary, indent=2) if args.json else render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
