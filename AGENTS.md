# AGENTS.md — instructions for coding agents (Codex, Claude Code, Cursor, …)

SpotBot: a Discord bot that turns pasted Instagram/TikTok reels into votable "spot
cards" and plans outings from group chat. Python 3.11+ (CI uses 3.12). This is a
four-person CSULB capstone — the human who asked you for a change must be able to
explain every line of it, so keep changes small and say what you did and why.

Start of every task: read `docs/KICKOFF.md` (team handoff) and, for anything
structural, `docs/ARCHITECTURE.md`.

## Setup

Linux / macOS / Codex cloud:
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```
Windows (PowerShell): `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`,
then use `.\.venv\Scripts\python.exe` in place of `python` below.

## Checks — run before you say a task is done

```bash
python -m pytest -k "not live" -q                        # offline suite, must stay green (324+)
python -m src.ingestion.eval --offline --split test      # accuracy scorecard (Track B work)
```
Neither needs a Discord token, Ollama, or internet (the suite does need Chromium).
Add or update tests for any behaviour you change; tests live next to their area
(`test_*.py` at the root, `app/test_*.py` for the bot).

**Do not run** `test_ig_live.py` or anything marked `live` inside a sandbox — they hit
real Instagram and need internet. They run on a teammate's own machine.

## Hard rules

- **Never commit** `.env`, anything under `data/`, `fixtures/labels.batch*.jsonl`,
  `fixtures/labels.inbox.jsonl`, or any token, password, or session cookie. Never put
  secrets in code or tests.
- **No paid APIs and no sending post text off the machine.** The stack is Ollama (local),
  ChromaDB (on disk), OpenStreetMap. Don't add OpenAI/Anthropic/Google API calls to the
  product path. (ADR-0002, `docs/SRS.md` C-1/C-2.)
- **Always pass `encoding="utf-8"`** when reading or writing text files (and
  `newline="\n"` when writing corpus/JSON files). Windows defaults to cp1252 and crashes
  on the label corpus.
- **Don't change files outside the task's track** without saying so explicitly in your
  summary (see ownership below). `app/` ships as its own Docker image and **cannot import
  from `src/`** — mirror small helpers instead (e.g. `app/cards.py` mirrors
  `src/ingestion/serving/discord_format.py`).
- The embedding model must be the same everywhere (`IngestionSettings.embed_model`,
  currently `mxbai-embed-large`). Don't hardcode another one.
- The bot must never ping: keep `allowed_mentions=discord.AllowedMentions.none()` on the
  client (`docs/THREAT_MODEL.md` T1).
- Retry policy lives in `serving/jobs.py::retryable_urls` and `is_transient_error`;
  don't retry anything outside them.
- **Every catalog call is tenant-authorized.** Any new bot → `:8010`/`:8003` request
  that names a `guild_id` must send `headers=tenant_headers(guild_id)` (with
  `user_id=` when it acts for a user, like a vote), and any new endpoint that takes a
  `guild_id` must call `authorize(...)` first and get a case in `test_admin_authz.py`
  and `scripts/bench_admin_authz.py`. Keep `app/tenant_auth.py` in step with
  `src/ingestion/serving/tenant_auth.py`. Never log a token. (`docs/THREAT_MODEL.md` T2.)

## The label corpus (`fixtures/labels.jsonl`) — Track B

- One JSON object per line, UTF-8, LF. Split lines with `split("\n")`, never
  `splitlines()` (captions contain U+2028).
- `scripts/review.py` rewrites the whole file — never run it while anything else writes
  the corpus.
- After any label change: `python scripts/build_aliases.py` (rebuilds both alias tables),
  then raise the floors in `test_extraction_labels.py` to the new `--split train`
  numbers. Floors only ever go up.
- **Tune on `--split train`. Do not iterate against `--split test`** — it is the held-out
  number the project reports. Report any claimed gain with its confidence interval
  (`python scripts/eval_diagnostics.py`).

## Who owns what

| Track | Paths |
|---|---|
| A — Capture | `src/ingestion/browser/`, `sources/`, `extractors/`, `pipeline/transcriber.py`, `pipeline/ocr.py`, `test_ig_*.py`, `test_tiktok.py` |
| B — Accuracy | `pipeline/normalizer.py`, `handle_split.py`, `src/ingestion/eval.py`, `fixtures/`, `scripts/{review,build_aliases,eval_diagnostics}.py`, `test_extraction_labels.py`, `serving/recommender.py` |
| C — Experience | `app/` (bot, cards, their tests), web pages in `serving/admin.py`, `docs/USER_GUIDE.md` |
| Nick — Platform | `pipeline/orchestrator.py`, `serving/admin.py` API, `serving/app.py`, `serving/jobs.py`, `sinks/`, `config.py`, CI, Docker, `docs/adr/`, `docs/ARCHITECTURE.md` |

## Commits and PRs

- Conventional commits: `feat(scope): …`, `fix(scope): …`, `test: …`, `docs: …`.
- One task per PR, small diffs. The PR description says what changed, why, how it was
  tested (paste the pytest summary line), and anything you were unsure about.
- Every PR needs one approval from a teammate who didn't write it.
- Don't reformat or "clean up" code you weren't asked to touch.

## Where to look

`docs/KICKOFF.md` (start) · `docs/ARCHITECTURE.md` (system) · `docs/adr/` (why) ·
`docs/SRS.md` (requirements) · `docs/TEAM_TODO.md` (backlog) · `docs/RUNBOOK.md` (running
it) · `docs/EXTRACTION_ACCURACY.md` + `docs/HANDOFF.md` (accuracy work) ·
`docs/THREAT_MODEL.md` (security).
