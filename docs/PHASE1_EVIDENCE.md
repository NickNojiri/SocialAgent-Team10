# Phase 1 evidence (Sep 15 – Oct 3, 2026)

The phase deliverable is this bundle: one number per track, each with the command that
reproduces it. A number without a command doesn't go here. Each track fills in its own
section; Nick collects them for the phase review. Dates are the plan's; confirm them
against Canvas.

**Status on 2026-10-14** (the phase closed Oct 3; collected late, in one pass, into
`docs/BASELINE.md`):

| Track | Status | What's still missing |
|---|---|---|
| A · Capture | partial | the logged-out pass rate on ≥50 unpicked reels with its failure breakdown (`capture_harness.py`), and the download-limit tests (#3) |
| B · Accuracy | done, except agreement | a blind second label on 40 reels for kappa (Nick) |
| C · Experience | not started | the `/setup` run and time to first card |
| D · Security | done | — |
| Platform | done, except capture timings | a real capture log for `summarize_captures.py` |

## A · Capture: #1 live harness, #3 SSRF + download limits

| | |
|---|---|
| Command | `pytest test_ig_live.py` (19 hand-picked links); `scripts/capture_harness.py` for unpicked reels is **still to be written (Track A)** |
| URLs | 19 hand-picked (2026-10-04). Unpicked: **pending** — the 220 links from the Instagram export qualify |
| Pass rate | hand-picked 19/19, 100% [83.2, 100]; unpicked logged-out pass rate **pending** |
| Unpicked reels we do have | 200 pulled from the DMs via the logged-in source (2026-10-08): caption 200/200 [98.1, 100], location tag 51.0% [44.1, 57.8], transcript 90.5% [85.6, 93.8], a real place 88.0% [82.8, 91.8] (`python experiments/baseline_report.py` §8; failed fetches weren't logged) |
| Failure breakdown | hand-picked: 0 failures. Unpicked: **pending** |
| Artifact for /dash | **pending** |
| SSRF tests | from Track D's suite (`test_attack_internal_address_ssrf.py`): internal URL at the API — pass (10/10); internal URL in the browser — **fail** (5); hostname resolving to a private IP — **fail**; redirect to internal in the embed fallback — **fail**; redirect to internal in the video download — **fail**; `169.254.169.254` — pass at the API, **fail** in the browser; oversized download and bad content type — **no test yet** (Track A #3) |
| Handoffs | Outbound-fetch list → D *(due ~Sep 24)* · taxonomy + artifact path → B |

## B · Accuracy: #4 scorecard, #5 label provenance + agreement

| | |
|---|---|
| Command | `python -m src.ingestion.eval --offline --split test` |
| Held-out numbers | venue same place 58.3% [50.9, 65.3] (n=175) · exact 45.1% [38.0, 52.5] · category 82.2% [76.0, 87.1] (n=180) · city 67.1% [59.3, 74.1] · promo rejected 33.3% [16.3, 56.3] (n=18). Full table: `docs/BASELINE.md` |
| Before/after mode | `--compare-stored`: stored at capture → now, same place 46.3% → 58.3%, McNemar p = 0.0025. `python experiments/compare_commits.py --before bbb74211`: rules baseline → now, 54.9% → 58.3%, p = 0.21 (not significant on test; +7.1, p < 0.0001 on train) |
| Labeler field | 358 / 633 rows, 56.6% [52.7, 60.4] |
| Agreement | **not measured yet**: no blind second labels. Non-blind review agreement 78/91, 85.7% [77.1, 91.5] (an upper bound). Pending: Nick's blind 40-reel sheet |

## C · Experience: #8 `/setup`

| | |
|---|---|
| Run | **pending — Track C**: fresh server, date, who ran it |
| Time to first card | **pending — Track C**: mm:ss, stopwatch, with a recording or written log |
| Setting keys | `drop_channel_id`, `home_city`, `home_lat`, `home_lng`, `updated_at` (from `guild_settings.py`; see `docs/handoffs/TRACK-C-BOT-CHANGES.md`) |

## D · Security: #12 attack suite

| | |
|---|---|
| Command | `python scripts/bench_security_attacks.py` |
| Result | 66 attacks, 58 refused, 8 got through (2026-10-14) |
| Coverage | yes — the suite fails when a protected endpoint has no attack case |
| In CI | yes — its own CI step since 2026-10-14; the attack tests also run in the offline suite |
| Got through | 8, all Track A: browser navigation to internal addresses (5), DNS to a private IP, embed redirect, video-download redirect. Status per probe: `docs/tracks/TRACK-D-SSRF-FINDINGS.md` |

## Platform (Nick): #9, #23, #24, #25

The offline suite and the authorization benchmark were rerun on 2026-10-14; the queue
measurement is from 2026-09-26. Earlier auth
and secret-scan evidence retains its original environment and date.

| What | Command | Result |
|---|---|---|
| Offline suite | `python -m pytest -k "not live" -q` | 856 passed, 6 deselected, 8 xfailed (2026-10-14; was 667 on 2026-09-26) |
| Cross-tenant authorization (#9) | `python scripts/bench_admin_authz.py` | 47/47 forged requests rejected, 23/23 authentic accepted (rerun 2026-10-14, unchanged) |
| Queue capacity (#29 tool, stub capture) | `python scripts/load_test_jobs.py --json` | 1 worker, 50 waiting: 51 accepted and 9 refused at burst 60 (capacity 51 in flight); p95 2.765 s and 17.6 jobs/s with a 0.05 s stub. Raw aggregate: [`docs/results/platform-queue-load-2026-09-26.json`](results/platform-queue-load-2026-09-26.json) |
| Capture timings (#23) | `python scripts/summarize_captures.py --output docs/results/capture-summary-YYYY-MM-DD.json` | *not yet: no real capture log or job database was present on 2026-09-26; needs real captures from Track A's harness run* |
| Secrets in the repo | `gitleaks git . --log-opts="--all" --redact` (v8.21.2), then `gitleaks dir .` | No leaks in 232 commits across all branches, or in the working tree. A `.env` was committed on 2026-03-15 (`a9c97245`) but was empty (0 bytes) and was later deleted (`20f8b016`). A `.venv/` committed the same day only bloats the history |
| Crash recovery (#24), dedup (#25) | `python -m pytest test_jobs.py -q` | Covered by tests; ADR-0005's Evidence section waits on real capture numbers |

The load test measures the queue only, with a sleep-only stub, so the throughput isn't a
capture speed. With real captures at about 60 s each, the finding recorded in
`docs/PAIRING.md` stands: the bot stops waiting after `INGEST_WAIT_S` = 900 s, so only
about 15 queued captures can finish in time on one worker, not 50. Setting
`INGEST_MAX_QUEUED` is Nick's decision.
