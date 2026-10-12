# Track D — Feature #12 SSRF findings

Each row is one probe in `test_attack_internal_address_ssrf.py`, with its owner and
status. As of 2026-10-12: **18 probes, 10 refused, 8 open** (all 8 Track A);
`python scripts/bench_security_attacks.py` prints **66 attacks, 58 refused, 8 got
through** across every attack class.

The 8 open probes are marked `xfail(strict=True)`, so CI stays green while they are
open and turns red the moment one is fixed: then delete that test's mark, and set the
row here to refused.

| # | Probe | Test area | Owner | Ownership basis | Status |
|---:|---|---|---|---|---|
| 1 | `POST /api/ingest` with `http://127.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 2 | `POST /api/ingest` with `http://localhost/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 3 | `POST /api/ingest` with `http://10.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 4 | `POST /api/ingest` with `http://169.254.169.254/latest/meta-data/` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 5 | `POST /api/ingest` with `http://[::1]/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 6 | `POST /api/jobs` with `http://127.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 7 | `POST /api/jobs` with `http://localhost/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 8 | `POST /api/jobs` with `http://10.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 9 | `POST /api/jobs` with `http://169.254.169.254/latest/meta-data/` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 10 | `POST /api/jobs` with `http://[::1]/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. | refused — `serving/admin.py::_internal_target` (88477a97) |
| 11 | Browser fetch of `http://127.0.0.1/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | open — `xfail(strict)` |
| 12 | Browser fetch of `http://localhost/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | open — `xfail(strict)` |
| 13 | Browser fetch of `http://10.0.0.1/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | open — `xfail(strict)` |
| 14 | Browser fetch of `http://169.254.169.254/latest/meta-data/` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | open — `xfail(strict)` |
| 15 | Browser fetch of `http://[::1]/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | open — `xfail(strict)` |
| 16 | `https://spot.example/reel/abc` mocked to resolve to `10.0.0.7` | DNS/private-address validation | Track A | Track A owns `src/ingestion/browser/`; DNS is mocked in the test. | open — `xfail(strict)` |
| 17 | Instagram embed URL returns a 302 to `http://127.0.0.1/admin` | Redirect validation in embed fallback | Track A | Track A owns `src/ingestion/browser/`, including `ig_embed.py`. | open — `xfail(strict)` |
| 18 | Video URL returns a 302 to `http://169.254.169.254/latest/meta-data/` | Redirect validation in video download | Track A | Track A owns `pipeline/transcriber.py`. | open — `xfail(strict)` |

The repository ownership table supports every assignment: Track A owns browser fetching
and `pipeline/transcriber.py`; Nick owns the admin API in `serving/admin.py`. No finding
in this set is unassigned.

The API rows (1–10) refuse a link that *names* an internal host. A public hostname
that resolves or redirects to an internal address is only caught at fetch time,
which is rows 16–18 (Track A).
