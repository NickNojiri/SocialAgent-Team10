"""How slow is capture, and how often do we capture the same reel twice?

Reads the capture log the job queue appends to (one JSON line per finished
job, `data/capture_jobs.jsonl` by default) and prints the duration
distribution, how many captures ran past the budget, per-stage p50/p95, fixed
failure-reason counts, and duplicate captures.

    python scripts/summarize_captures.py
    python scripts/summarize_captures.py --log data/capture_jobs.jsonl --json

Feature #23. The log holds links and timings only — no tokens, no user ids,
no message text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The logic lives in the service so /dash shows the same numbers (#29).
from src.ingestion.serving.capture_stats import (  # noqa: E402,F401
    THRESHOLDS_S,
    percentile,
    read_rows,
    summarize,
)

DEFAULT_LOG = Path("data/capture_jobs.jsonl")


def render(summary: dict) -> str:
    if not summary["captures"]:
        return "No captures logged yet. Captures are logged on the async path (the default); check CAPTURE_LOG isn't off."
    d, dup = summary["duration_s"], summary["duplicates"]
    lines = [
        f"captures            {summary['captures']}  ({', '.join(f'{k}={v}' for k, v in sorted(summary['states'].items()))})",
        f"duration (s)        median {d['median']}  p90 {d['p90']}  p95 {d['p95']}  max {d['max']}",
        "over budget         " + "  ".join(
            f"{n} past {t}" for t, n in summary["over_threshold"].items()
        ),
        "stage p50/p95 (s)   " + ("  ".join(
            f"{k} {v['p50']}/{v['p95']} (n={v['samples']})"
            for k, v in summary["stage_s"].items()
        ) or "none recorded"),
        "failure reasons     " + ("  ".join(
            f"{k}={v}" for k, v in summary["failure_reasons"].items()
        ) or "none"),
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
