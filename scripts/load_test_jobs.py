"""How much load can the capture queue take before it slows down or says no?

    python scripts/load_test_jobs.py
    python scripts/load_test_jobs.py --workers 2 --service-s 0.2 --levels 1,5,10,25,50,75
    python scripts/load_test_jobs.py --json

Feature #29's load test. It drives the real JobQueue (src/ingestion/serving/jobs.py)
with a *stub* capture that just sleeps for --service-s and returns a fake result, so it
is offline and safe: no browser, no Instagram, no Ollama, no network. What it measures
is the queue itself — how latency grows with the backlog, and at what burst size
captures start being refused (the 429 a real bot sees).

For each level N, N captures arrive one at a time, as separate paste requests would,
and the run waits for all accepted ones to finish. Report per level: accepted, refused,
median / p95 / max time from submit to done, and throughput.

Reading the result: real captures take 30–180 s, not --service-s. Latencies scale with
the service time, so divide by --service-s to read them in "captures' worth of wait".
The refusal point doesn't depend on the service time at all: it is max_queued waiting
+ one running per worker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The one nearest-rank percentile (it had an off-by-one; fixed there, 2026-09-24).
from src.ingestion.serving.capture_stats import percentile  # noqa: E402,F401
from src.ingestion.serving.jobs import JobQueue, QueueFull  # noqa: E402

DEFAULT_LEVELS = (1, 5, 10, 25, 50, 60, 100)


async def run_level(
    n: int,
    *,
    workers: int,
    max_queued: int,
    service_s: float,
    jitter: float = 0.0,
    seed: int = 0,
) -> dict:
    """Submit `n` captures one after another into a fresh queue; wait for them all."""
    rng = random.Random(seed)

    async def stub_capture(urls, guild_id, on_stage):
        for url in urls:
            on_stage(url, "fetching")
        spread = service_s * jitter
        await asyncio.sleep(max(0.0, service_s + rng.uniform(-spread, spread)))
        for url in urls:
            on_stage(url, "done")
        return {"added": len(urls), "rejected": 0, "unreadable": 0, "events": [], "log": []}

    # Retries off and no log/store: this measures the queue, not the retry policy.
    queue = JobQueue(stub_capture, workers=workers, max_queued=max_queued, max_retries=0)
    submitted: dict[str, float] = {}
    refused = 0
    t0 = time.perf_counter()
    for i in range(n):
        try:
            # Distinct links: the same link twice would join the first capture (#25).
            job = queue.submit([f"https://load.test/reel/{i}"], "load-test")
            submitted[job.id] = time.perf_counter()
        except QueueFull:
            refused += 1
        await asyncio.sleep(0)            # the next paste is a separate request
    await queue.drain()
    wall = time.perf_counter() - t0
    queue.shutdown()

    # Each job's own record: created_at → finished_at, i.e. submit to done.
    latencies = [queue.get(j).duration_s for j in submitted]
    return {
        "level": n,
        "accepted": len(submitted),
        "refused": refused,
        "latency_s": {
            "median": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "throughput_per_s": round(len(submitted) / wall, 2) if wall > 0 else 0.0,
    }


async def run(levels, **kw) -> dict:
    results = [await run_level(n, **kw) for n in levels]
    first_refusal = next((r["level"] for r in results if r["refused"]), None)
    return {
        "config": {k: kw[k] for k in ("workers", "max_queued", "service_s", "jitter")},
        "capacity": kw["max_queued"] + kw["workers"],
        "first_level_with_refusals": first_refusal,
        "levels": results,
    }


def render(report: dict) -> str:
    c = report["config"]
    s = c["service_s"]
    lines = [
        f"queue: {c['workers']} worker(s), up to {c['max_queued']} waiting; "
        f"stub capture takes {s}s (±{int(c['jitter'] * 100)}%)",
        f"capacity: {report['capacity']} captures in flight at once, the rest are refused (429)",
        "",
        f"{'burst':>6} {'accepted':>9} {'refused':>8} {'median':>9} {'p95':>9} {'max':>9}"
        f" {'p95 in captures':>16} {'per sec':>8}",
    ]
    for r in report["levels"]:
        lat = r["latency_s"]
        waits = lat["p95"] / s if s else 0.0
        lines.append(
            f"{r['level']:>6} {r['accepted']:>9} {r['refused']:>8} {lat['median']:>8.2f}s"
            f" {lat['p95']:>8.2f}s {lat['max']:>8.2f}s {waits:>15.1f}x {r['throughput_per_s']:>8}"
        )
    first = report["first_level_with_refusals"]
    lines += [
        "",
        "first refusals at a burst of "
        + (str(first) if first is not None else "— none in these levels"),
        "p95 in captures = how many captures' worth of time the slowest 5% waited. With a real "
        "capture at ~60 s, multiply by 60 for seconds.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workers", type=int, default=1, help="INGEST_WORKERS (default 1, as shipped)")
    ap.add_argument("--max-queued", type=int, default=50, help="waiting jobs before 429 (default 50)")
    ap.add_argument("--service-s", type=float, default=0.05, help="stub capture time (default 0.05)")
    ap.add_argument("--jitter", type=float, default=0.2, help="± fraction of service time (default 0.2)")
    ap.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)),
                    help="comma-separated burst sizes")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    report = asyncio.run(run(levels, workers=max(1, args.workers), max_queued=args.max_queued,
                             service_s=args.service_s, jitter=args.jitter, seed=args.seed))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
