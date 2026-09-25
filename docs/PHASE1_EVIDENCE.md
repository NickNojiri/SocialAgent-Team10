# Phase 1 evidence (Sep 15 – Oct 3, 2026)

The phase deliverable is this bundle: one number per track, each with the command that
reproduces it. A number without a command doesn't go here. Each track fills in its own
section; Nick collects them for the phase review. Dates are the plan's; confirm them
against Canvas.

## A · Capture: #1 live harness, #3 SSRF + download limits

| | |
|---|---|
| Command | `python scripts/capture_harness.py …` *(to be written)* |
| URLs | *N reels, how they were chosen (not hand-picked)* |
| Pass rate | *k / N, with a 95% interval* |
| Failure breakdown | `login_wall` · `no_video` · `timeout` · `no_venue` · `not_a_place` · `other` |
| Artifact for /dash | *path to the JSON/CSV* |
| SSRF tests | *internal URL, redirect to internal, oversized, bad content type, `169.254.169.254` before any request: pass/fail each* |
| Handoffs | Outbound-fetch list → D *(due ~Sep 24)* · taxonomy + artifact path → B |

## B · Accuracy: #4 scorecard, #5 label provenance + agreement

| | |
|---|---|
| Command | `python -m src.ingestion.eval --offline --split test` |
| Held-out numbers | *each metric with its 95% Wilson interval* |
| Before/after mode | *command, and McNemar p-value on one real change* |
| Labeler field | *rows with a labeler / total rows* |
| Agreement | *Cohen's kappa on the double-labeled slice, n = …* |

## C · Experience: #8 `/setup`

| | |
|---|---|
| Run | *fresh server, date, who ran it* |
| Time to first card | *mm:ss, stopwatch, with a recording or written log* |
| Setting keys | `drop_channel_id`, `home_city`, `home_lat`, `home_lng`, `updated_at` (from `guild_settings.py`; see `docs/handoffs/TRACK-C-BOT-CHANGES.md`) |

## D · Security: #12 attack suite

| | |
|---|---|
| Command | *one command* |
| Result | *N attacks, M refused, K got through* |
| Coverage | *every protected endpoint has a case: yes/no* |
| In CI | *yes/no* |
| Got through | *list with owners, or "none"* |

## Platform (Nick): #9, #23, #24, #25

Measured 2026-09-24 on `main` @ `323e0467` in a Linux cloud container (Python 3.11):

| What | Command | Result |
|---|---|---|
| Offline suite | `python -m pytest -k "not live" -q` | 664 passed, 6 deselected |
| Cross-tenant authorization (#9) | `python scripts/bench_admin_authz.py` | 47/47 forged requests rejected, 23/23 authentic accepted |
| Queue capacity (#29 tool, stub capture) | `python scripts/load_test_jobs.py` | 1 worker, 50 waiting: first 429 at a burst of 60 (capacity 51 in flight); ~19 captures/s with a 0.05 s stub |
| Capture timings (#23) | `python scripts/summarize_captures.py` | *not yet: needs real captures from Track A's harness run* |
| Secrets in the repo | `gitleaks git . --log-opts="--all" --redact` (v8.21.2), then `gitleaks dir .` | No leaks in 232 commits across all branches, or in the working tree. A `.env` was committed on 2026-03-15 (`a9c97245`) but was empty (0 bytes) and was later deleted (`20f8b016`). A `.venv/` committed the same day only bloats the history |
| Crash recovery (#24), dedup (#25) | `python -m pytest test_jobs.py -q` | Covered by tests; ADR-0005's Evidence section waits on real capture numbers |

The load test measures the queue only, with a sleep-only stub, so the throughput isn't a
capture speed. With real captures at about 60 s each, the finding recorded in
`docs/PAIRING.md` stands: the bot stops waiting after `INGEST_WAIT_S` = 900 s, so only
about 15 queued captures can finish in time on one worker, not 50. Setting
`INGEST_MAX_QUEUED` is Nick's decision.
