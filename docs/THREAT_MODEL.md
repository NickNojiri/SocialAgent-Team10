# SpotBot — Threat model

Written 2026-09-15 (Sprint 3 deliverable, done early). Owner: Nick. Scope: the
self-hosted deployment in `docs/ARCHITECTURE.md`. Method: STRIDE over the four trust
boundaries in ARCHITECTURE §6, with the LLM-specific items mapped to the
[OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/).

Every finding names the file and line it was verified against on `main` at
`3ba74b19`. Status legend: **fixed** (with the commit) · **built, uncommitted** ·
**open** (with owner and sprint) · **accepted** (with the reason).

---

## 1. What we are protecting

| Asset | Why it matters |
|---|---|
| The Discord server's attention | the bot can post in any channel it can read; an unwanted `@everyone` is the fastest way to get it kicked |
| Each server's catalog | spots, votes, voter names, attended nights — private to that group |
| The operator's machine | the admin app drives a headless browser and downloads video on request |
| Secrets | `DISCORD_TOKEN`, IG burner credentials + session cookie |
| The accuracy benchmark | `fixtures/labels.jsonl` is what every claim in the report rests on |

## 2. Trust boundaries and who sits on each side

| Boundary | Untrusted side | What crosses |
|---|---|---|
| **B1** Discord → bot | anyone in any server the bot is in, incl. DMs | messages with links, button presses, modal text, slash commands |
| **B2** internet → pipeline | the page behind a pasted URL: caption, transcript, on-screen text, og tags, video bytes | everything the extractor reads |
| **B3** LLM → store | Ollama's output | venue / theme / summary text that lands in Chroma and later on cards |
| **B4** network → `:8010` / `:8003` | whoever can reach the ports | `guild_id`, URLs, votes, deletes |

---

## 3. Findings

### T1 · Mention injection — scraped or user-typed text becomes a ping · B1/B2 · **fixed**

**Attack.** `venue_name` is rendered into *plain message content* in several places:
the went-there prompt (`app/bot.py:126-128`), `/catalog` (`bot.py:393-397`), and the
recommendation markdown for `/events` and ambient suggestions
(`bot.py:169`, `:362`, built in `serving/discord_format.py:36`). Discord embeds never
ping; plain content does. The client was created as `discord.Client(intents=intents)`
(`bot.py:69`) with no `allowed_mentions`, so Discord's default applied — `@everyone`
pings if the bot has the permission. Two ways to get the string in:

- **Directly:** the *Add manually* and *Edit* modals accept any venue text;
  `_manual_record("@everyone", …)` is accepted by the validator (verified — only
  platform boilerplate like "Locations" is rejected, `schemas/inspiration.py:92`).
  Any server member can then trigger the ping with `/catalog`.
- **Indirectly:** a reel caption designed to survive the slot parser.

**Fix.** `discord.Client(intents=…, allowed_mentions=discord.AllowedMentions.none())`.
In discord.py 2.7.1 the client default is merged into channel sends *and* interaction
follow-ups (`discord/webhook/async_.py:1851`), so every path is covered; `thread.mention`
in `/plan`'s confirmation is a channel mention and is unaffected. Test:
`app/test_bot.py::test_client_never_pings_from_content`.

**Residual.** Text can still *say* anything (abuse, links). Cards are removable by
anyone with Manage Messages; the share page escapes HTML (T7).

---

### T2 · Cross-tenant authorization — `guild_id` was caller-controlled · B4 · **fixed 2026-09-17**

**Attack.** `guild_id` selects the Chroma collection on every catalog endpoint. It was
a plain request field, so anything that could reach `:8010` or `:8003` could read,
wipe, edit or add to any server's catalog (DM stashes included), read another server's
capture results, swallow its went-there prompts, and vote or confirm a night out *as*
any `user_id` — three forged votes reach quorum and create a real Scheduled Event.
`docker-compose.yml` also published `:8003` on every interface.

**Fix.** HMAC-SHA256 tenant tokens (`serving/tenant_auth.py`, mirrored in
`app/tenant_auth.py` because the bot image cannot import `src/`):
`HMAC(SPOTBOT_SIGNING_KEY, "v1\n{scope}\n{guild_id}\n{user_id}")`, sent as the
`X-Tenant-Token` header.
- Every endpoint that takes a `guild_id` checks it — admin: events, nights, ingest,
  jobs (submit *and* status, checked against the job's own guild), manual, edit, lock,
  followups, went, vote, delete, `/share`; recommend: `/recommend`, `/plan`.
- Scopes: `rw` for the bot, read-only `r` for share links; `r` never satisfies a
  write. `followups` needs `rw` because it marks prompts as sent.
- Vote and went tokens are signed over the acting `user_id`.
- Missing key → **503, fail closed**. `scripts/ensure_signing_key.py` (run by
  `setup.ps1` and `run_local.ps1`) creates the key so a fresh setup never hits that.
- `:8003` is now published on `127.0.0.1` only; `run_local.ps1` binds both apps to
  `127.0.0.1` explicitly.

**Evidence.** `test_admin_authz.py` (endpoint-by-endpoint, plus a test that the bot's
mirror mints byte-identical tokens) and `scripts/bench_admin_authz.py`: **32/32**
forged requests rejected across 32 attack classes, **15/15** authentic requests
accepted. With `authorize` replaced by a no-op — the pre-fix code path — the same
32 forgeries are rejected **0/32**.

**Residual (accepted).** Tokens are stable bearer capabilities, not nonces — whoever
captures one keeps that tenant's access until the key is rotated, and rotating revokes
every share link. The empty tenant `""` (legacy single-tenant catalog, the local web
UI at `/`) stays open by design, and `/api/stats` + `/dash` show per-guild *counts*
across tenants; both rely on the `127.0.0.1` bind (T8).

---

### T3 · Server-side request forgery — the pipeline fetches whatever it is told · B4 · **open**

**Attack.** `/api/ingest` checks only the scheme (`admin.py:230`) and the session
manager only scheme + netloc (`browser/session_manager.py:127`). Playwright will
navigate to `http://127.0.0.1:8010/…`, `http://169.254.169.254/…` or a LAN host; the
`/embed/` fallback and the video download (`transcriber.py:102`, `follow_redirects=True`)
also fetch attacker-chosen hosts. The bot only forwards Instagram/TikTok URLs
(`cards.extract_capture_urls`), so this needs B4 access plus a valid tenant token
since T2 — or the open legacy `""` tenant from the same host.

**Mitigation to build.** Host allowlist at ingest (`instagram.com`, `tiktok.com`, later
`youtube.com`), and resolve-then-reject private/link-local addresses before the video
GET. Owner: Track A with Nick reviewing, Sprint 3. Existing controls that limit blast
radius: per-domain throttle, 50 MB download cap, `capture_budget_s`.

---

### T4 · Prompt injection into the extractor, summarizer and planner · B2/B3 · **accepted** with mitigations (OWASP LLM01, LLM02, LLM08)

**Attack.** Caption, transcript and on-screen text are untrusted and go into three
prompts: the field extractor (`pipeline/llm_extractor.py`), the quick-description
summarizer (`pipeline/summarizer.py`), and — via the chat transcript `/plan` collects
(`bot.py:504-510`) — `recommender.synthesize_request`. Goal of an attacker: make the
bot store or say something it should not, or bias recommendations.

**Mitigations in place.**
- Extractor output is constrained: JSON only, enum category, and
  `_strip_hallucinations` discards any venue or location string not present verbatim
  in the input (`llm_extractor.py:159-167`). Heuristics decide first (ADR-0002).
- Summaries are capped (`num_predict` 120; schema `max_length=600`) and display-only.
- `/plan` degrades to keyword extraction on any parse failure; its output is a small
  dict used as a query, not executed.
- Nothing an LLM emits is passed to a shell, a URL fetch, or another tool.

**Residual (accepted).** Injected text can *appear* on a card (`core_theme`, `summary`)
and can shift embeddings (`chroma_sink._vibe_document`), nudging what `/events`
returns. Impact is a bad card, which Remove fixes. Re-evaluate if a hosted LLM is ever
wired in (ADR-0002 follow-up 3): then LLM06 (sensitive disclosure) applies too.

---

### T5 · Resource exhaustion — link floods on a CPU-only box · B1/B4 · **open, partially mitigated** (OWASP LLM10)

**Attack.** Paste many links, or many messages; each capture costs a browser
navigation, a video download and a Whisper run.

**In place:** ≤ 10 URLs per request (`admin.py:148`), 180 s per-URL budget,
50 MB video cap, per-domain throttle, content-hash dedup at the Chroma upsert.
**Missing:** per-user and per-guild rate limits, a daily capture cap, dedup *before* the
fetch (`TEAM_TODO.md` P0.5). The async queue (ADR-0004) bounds concurrency and gives
rate limits a place to live. Owner: Nick, Sprint 3–4.

---

### T6 · Secrets and data at rest · **mostly in place**

`.env`, `data/` (Chroma, JSONL, `ig_session.json`, raw HTML) and `secrets/` are
gitignored. The `.gitignore` line meant to ignore `.claude/` was stored as
UTF-16 bytes and matched nothing — fixed 2026-09-15.

**Deletion — built 2026-09-24 (#21).** `/privacy` explains what is kept; Manage
Server can delete a server's data after typing its name (`POST /api/forget`, full
tenant token, confirm repeats the id). It removes the catalog, settings, feedback
and survey answers, capture jobs and log lines, capture JSONL, snapshots, activity
feed and rate counters, and
VACUUMs `chroma.sqlite3` and `jobs.db` — both kept deleted rows' bytes in free
pages until rebuilt (probed). `test_forget.py` byte-scans every file afterwards.
Remaining gaps: no *time-based* retention yet; the old shared
`data/inspirations.jsonl` and `data/raw/snapshot-*.html` from before per-server
files have no server id, so their rows go only when no other server saved the
same post, and old snapshots can't be attributed at all; on Windows the vector
index folder is removed at the next admin start, not at once.

---

### T7 · Cross-site scripting on the web pages · B3 → browser · **verified safe**

All three server-rendered pages escape scraped text before `innerHTML`
(`esc()` at `admin.py:703`, `:759`, `:875`), including venue, theme, voter names and
`source_url`. Keep it that way: any new field on a page goes through `esc()`.

---

### T8 · Network exposure of the admin API · B4 · **open on the always-on box**

Local runs bind `127.0.0.1` (`run_local.ps1` explicitly, `Makefile` by uvicorn's
default), and `docker-compose.yml` publishes `:8003` on `127.0.0.1` only. The
always-on box's systemd unit (not in git) binds `0.0.0.0:8010` so the Docker bot can
reach it via `host.docker.internal`. Since T2, every guild's data there needs a
token; what stays reachable is the open legacy `""` catalog and the per-guild counts
on `/api/stats` + `/dash`. Remaining fix on the box: set `SPOTBOT_SIGNING_KEY` in the
unit's environment (same value as the bot's `.env`), then bind `127.0.0.1` and reach it
over the docker bridge address, or keep `0.0.0.0` behind a firewall rule. Owner: Nick.

---

### T9 · Benchmark integrity · **process control**

The accuracy numbers are only as good as `fixtures/labels.jsonl`. Controls:
train/test split by URL hash, tuning on `train` only, CI floors on `train`, the
train-only alias table for scoring, and `review.py` as the single writer (it rewrites
the whole file). Open: per-row label provenance and a second annotator
(`ML_ADVISOR_BRIEF.md`).

---

## 4. Summary

| # | Finding | STRIDE | Status |
|---|---|---|---|
| T1 | mention injection via plain-content sends | Spoofing / Elevation | **fixed** — `AllowedMentions.none()` |
| T2 | caller-controlled `guild_id` | Elevation / Tampering / Info disclosure | **fixed** — HMAC tenant tokens on :8010 and :8003; 32/32 forgeries rejected (0/32 before) |
| T3 | SSRF through pasted URLs | Elevation | open — Track A + Nick, Sprint 3 |
| T4 | prompt injection into LLM stages | Tampering | accepted, mitigated |
| T5 | capture floods | DoS | partial — Nick, Sprint 3–4 |
| T6 | secrets / retention | Info disclosure | mostly in place; deletion built (#21), time-based retention open |
| T7 | XSS on web pages | Tampering | verified safe |
| T8 | `:8010` exposure on the box | Info disclosure | open — set the key in the systemd unit, then bind `127.0.0.1` |
| T9 | benchmark integrity | Repudiation | process controls in place |

Things deliberately *not* modelled: Discord's own security, Ollama's process
isolation, physical access to the box.
