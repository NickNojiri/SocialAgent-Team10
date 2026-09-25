# Handoff to Track D: the authorization benchmark (seed for #12)

From Nick (Platform), 2026-09-24. This is the "You → D" Phase 1 handoff in
`docs/tracks/TRACK-N-PLATFORM.md`. The attack runner itself is #12 and is yours; this
note only tells you what already exists so you don't start from zero.

## Run it

```bash
python scripts/bench_admin_authz.py
```

No Discord token, network, Ollama or real data needed. It drives the real FastAPI apps
(admin `:8010`, recommend `:8003`) in-process with storage stubbed. Result on `main`
today (`323e0467`):

```text
=> rejected 47/47 forged requests across 47 attack classes, accepted 23/23 authentic
```

It exits 1 if any forged request gets through or any authentic one is refused.

## How it works

- `mint_token(guild_id, scope=..., user_id=...)` in `src/ingestion/serving/tenant_auth.py`
  makes a real signed token. The bench mints tokens for two servers (`MINE`, `THEIRS`)
  and two users (`ME`, `YOU`).
- Tokens go in the `X-Tenant-Token` header (share links use `?t=`).
- Every endpoint that takes a `guild_id` calls `authorize(guild_id, token, need=...,
  user_id=...)` first. Scopes: `SCOPE_WRITE` (the bot's full token) and `SCOPE_READ`
  (share links, read-only). Votes and "went there" also need the token to name the user.
- A forged request counts as **refused** on 401 or 403; an authentic one counts as
  **accepted** on 200 or 202. Anything else is a failure either way.
- `test_admin_authz.py` holds the same cases as pytest tests (they run in CI). The bench
  is the readable report; the tests are the gate.

## What it covers today

| Your #12 attack class | Already in the bench? |
|---|---|
| Forged / tampered token | Yes: garbage, empty, bit-flipped, truncated |
| Server X's token against server Y | Yes, on every guild-scoped endpoint |
| Faked votes on someone else's card | Yes: vote/unvote as another user, token without a voter, vote in another guild, "went there" as another user |
| No token at all | Yes, on reads, deletes, ingest, jobs, share links, `/plan`, forget, time-to-card |
| Injected caption ("ignore previous instructions…") | **No.** Not an auth question; needs its own module |
| Link to an internal address (SSRF) | **No.** Depends on Track A's #3 and their list of outbound fetches |

## Every route, and whether it is guild-scoped

Listed from the running apps (`app.routes`), not from memory.

**Guild-scoped: every one has at least one forged case in the bench.** All but two also
have an authentic case. `POST /api/ingest` has none (only forged cases), and
`POST /api/jobs` is only used, not reported: the bench submits one real job during setup
and would crash if that were refused. A gate that also refused real ingests would not
show up in the bench's count. Worth adding both to your authentic list.

admin `:8010`: `GET /api/events` · `POST /api/ingest` · `POST /api/jobs` ·
`GET /api/jobs` · `GET /api/jobs/{job_id}` · `POST /api/manual` ·
`POST /api/events/{id}/edit` · `POST /api/events/{id}/lock` · `GET /api/followups` ·
`POST /api/events/{id}/went` · `GET /api/nights` · `GET /api/settings` ·
`PUT /api/settings` · `POST /api/feedback` · `POST /api/survey` · `GET /api/survey` ·
`POST /api/time-to-card` · `POST /api/forget` · `DELETE /api/events/{id}` ·
`POST /api/events/{id}/vote` · `GET /share`

recommend `:8003`: `POST /recommend` · `POST /plan`

**Not guild-scoped: no token by design. Worth a look from you**

- `GET /health` (both), `GET /ready` (recommend).
- `GET /api/stats` and `GET /dash`: the ops dashboard. Since `2738f29b`, `/api/stats`
  returns counts only; before that it leaked every server's captured URLs.
- `GET /`: the admin landing page.
- `GET /docs`, `/redoc`, `/openapi.json` on **both** services. That's FastAPI's default
  auto-generated API docs, which describe every endpoint to anyone who can reach the
  port. Whether to turn them off in staging is a finding for your report, not something
  Platform has decided.

## The gap #12 closes

The bench's list is **hand-written**. Nothing fails if someone adds a new guild-scoped
endpoint and forgets to add a case: that's the "enumerate every protected endpoint"
item in your #12. The route listing above (`for r in app.routes`) is one way to get the
list to check against.

## Rules from `AGENTS.md` that apply

- Any new endpoint taking a `guild_id` must call `authorize(...)` first and get a case
  in `test_admin_authz.py` **and** `scripts/bench_admin_authz.py`.
- Keep `app/tenant_auth.py` in step with `src/ingestion/serving/tenant_auth.py`.
- Never log or print a token.
- Attack scripts target localhost or our own staging host only.
