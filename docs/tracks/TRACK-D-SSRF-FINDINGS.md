# Track D — Feature #12 SSRF findings

Each row is one probe in `test_attack_internal_address_ssrf.py`, with its owner and
status. Rechecked 2026-11-08: **18 probes, 18 refused, 0 open**;
`python scripts/bench_security_attacks.py` prints **101 attacks, 101 refused, 0 got through**.
The eight Track A probes were closed by `src/ingestion/browser/url_guard.py` (the browser
checks a URL and its DNS answer before opening it; the embed fallback and the video
download check every redirect hop), so their `xfail` marks are gone.

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
| 11 | Browser fetch of `http://127.0.0.1/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | refused — `browser/url_guard.py` |
| 12 | Browser fetch of `http://localhost/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | refused — `browser/url_guard.py` |
| 13 | Browser fetch of `http://10.0.0.1/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | refused — `browser/url_guard.py` |
| 14 | Browser fetch of `http://169.254.169.254/latest/meta-data/` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | refused — `browser/url_guard.py` |
| 15 | Browser fetch of `http://[::1]/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. | refused — `browser/url_guard.py` |
| 16 | `https://spot.example/reel/abc` mocked to resolve to `10.0.0.7` | DNS/private-address validation | Track A | Track A owns `src/ingestion/browser/`; DNS is mocked in the test. | refused — `browser/url_guard.py` |
| 17 | Instagram embed URL returns a 302 to `http://127.0.0.1/admin` | Redirect validation in embed fallback | Track A | Track A owns `src/ingestion/browser/`, including `ig_embed.py`. | refused — `browser/url_guard.py` |
| 18 | Video URL returns a 302 to `http://169.254.169.254/latest/meta-data/` | Redirect validation in video download | Track A | Track A owns `pipeline/transcriber.py`. | refused — `browser/url_guard.py` |

The repository ownership table supports every assignment: Track A owns browser fetching
and `pipeline/transcriber.py`; Nick owns the admin API in `serving/admin.py`. No finding
in this set is unassigned.

The API rows (1–10) refuse a link that *names* an internal host. A public hostname
that resolves or redirects to an internal address is only caught at fetch time,
which is rows 16–18 (Track A).
