# Track D — Feature #12 SSRF findings

These findings correspond to the failing cases in
`test_attack_internal_address_ssrf.py`. They record the owner for each unresolved
probe; the attack tests remain failures until the owning implementation closes them.

| # | Failing probe | Test area | Owner | Ownership basis |
|---:|---|---|---|---|
| 1 | `POST /api/ingest` with `http://127.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 2 | `POST /api/ingest` with `http://localhost/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 3 | `POST /api/ingest` with `http://10.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 4 | `POST /api/ingest` with `http://169.254.169.254/latest/meta-data/` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 5 | `POST /api/ingest` with `http://[::1]/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 6 | `POST /api/jobs` with `http://127.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 7 | `POST /api/jobs` with `http://localhost/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 8 | `POST /api/jobs` with `http://10.0.0.1/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 9 | `POST /api/jobs` with `http://169.254.169.254/latest/meta-data/` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 10 | `POST /api/jobs` with `http://[::1]/admin` | API URL validation | Nick / API | Nick owns `src/ingestion/serving/admin.py` API implementation. |
| 11 | Browser fetch of `http://127.0.0.1/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. |
| 12 | Browser fetch of `http://localhost/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. |
| 13 | Browser fetch of `http://10.0.0.1/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. |
| 14 | Browser fetch of `http://169.254.169.254/latest/meta-data/` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. |
| 15 | Browser fetch of `http://[::1]/admin` | URL validation before browser navigation | Track A | Track A owns `src/ingestion/browser/`. |
| 16 | `https://spot.example/reel/abc` mocked to resolve to `10.0.0.7` | DNS/private-address validation | Track A | Track A owns `src/ingestion/browser/`; DNS is mocked in the test. |
| 17 | Instagram embed URL returns a 302 to `http://127.0.0.1/admin` | Redirect validation in embed fallback | Track A | Track A owns `src/ingestion/browser/`, including `ig_embed.py`. |
| 18 | Video URL returns a 302 to `http://169.254.169.254/latest/meta-data/` | Redirect validation in video download | Track A | Track A owns `pipeline/transcriber.py`. |

The repository ownership table supports every assignment: Track A owns browser fetching
and `pipeline/transcriber.py`; Nick owns the admin API in `serving/admin.py`. No finding
in this set is unassigned.
