"""scripts/load_test_jobs.py — the offline queue load test (feature #29).

Fast: the stub capture sleeps milliseconds. What's checked is that the report tells
the truth about the queue — where refusals start and that waits grow with the backlog.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "load_test_jobs", Path(__file__).parent / "scripts" / "load_test_jobs.py"
)
ltj = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ltj
_spec.loader.exec_module(ltj)

FAST = {"service_s": 0.005, "jitter": 0.0}


def test_refusals_start_exactly_past_waiting_plus_running():
    r = asyncio.run(ltj.run_level(6, workers=1, max_queued=3, **FAST))
    assert (r["accepted"], r["refused"]) == (4, 2)          # 3 waiting + 1 running

    two = asyncio.run(ltj.run_level(8, workers=2, max_queued=3, **FAST))
    assert (two["accepted"], two["refused"]) == (5, 3)       # 3 waiting + 2 running


def test_waits_grow_with_the_backlog():
    small = asyncio.run(ltj.run_level(1, workers=1, max_queued=50, **FAST))
    big = asyncio.run(ltj.run_level(10, workers=1, max_queued=50, **FAST))
    assert big["latency_s"]["p95"] > 3 * small["latency_s"]["p95"]
    assert big["refused"] == 0 and big["accepted"] == 10


def test_the_report_names_capacity_and_the_first_refusal():
    report = asyncio.run(ltj.run([1, 3, 6], workers=1, max_queued=3, **FAST))
    assert report["capacity"] == 4
    assert report["first_level_with_refusals"] == 6
    text = ltj.render(report)
    assert "capacity: 4" in text and "first refusals at a burst of 6" in text


def test_json_output_parses(capsys):
    assert ltj.main(["--levels", "1,2", "--service-s", "0.002", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [lvl["level"] for lvl in report["levels"]] == [1, 2]
    assert report["config"]["workers"] == 1                  # the shipped default
