# ADR-0007: Reels reach SpotBot through Instagram's official Messaging API, not a logged-in burner

- **Status:** accepted (Platform side: webhook app, store, launch and check scripts) · proposed to Track A (`sources/ig_webhook.py` shape)
- **Decided:** 2026-10-24 · **Recorded:** 2026-10-24 · **Owner:** Nick
- **Supersedes:** the logged-in (`authed`) rung of ADR-0006 as a recommended path
- **Related:** `docs/handoffs/OFFICIAL-INSTAGRAM-API-PLAN.md`, `docs/handoffs/TRACK-A-COMPLIANT-CAPTURE.md`,
  `docs/THREAT_MODEL.md` T8 and T10, `serving/ig_webhook_app.py`, `sources/ig_webhook.py`

## Context

The burner account (instagrapi, logged in as a person) was flagged on 2026-10-09, challenged on
2026-10-23 after ~1,000 paced calls, and **suspended** on 2026-10-24. Logged-in automation breaks
Instagram's Terms of Use at any pace, so a replacement burner would end the same way, and "we
relied on a method the platform prohibits" is a finding against a graded project.

Meta's Instagram Messaging API delivers messages sent to a **professional** account as webhooks.
A shared reel arrives as an `ig_reel` (or `reel`) attachment with the reel's title (caption), url
and video id; a shared post as `ig_post` (media id, url, title). That is the same "DM SpotBot a
reel" flow the burner powered, with Meta's permission.

## Options considered

1. **Another burner** — rejected: same terms violation, same ending.
2. **Paid scraping API** (Apify/RapidAPI) — rejected: costs money (ADR-0002) and moves the terms
   question to a vendor rather than answering it.
3. **oEmbed only** — kept as a later rung: needs App Review and since Nov 2025 returns no author
   or thumbnail, so it adds little over the `/embed/` fetch.
4. **Messaging API webhook into the existing capture queue** — *chosen*.

## Decision

- A separate FastAPI app, `serving/ig_webhook_app.py` on `127.0.0.1:8011`, is the only thing a
  tunnel or proxy exposes. It answers Meta's handshake, **verifies `X-Hub-Signature-256` before
  parsing anything**, caps bodies at 256 KB, limits each sender to 20 captures an hour, returns
  200 at once, and then queues one capture job per shared post on the admin app over localhost
  (signed with `tenant_headers` when `IG_WEBHOOK_GUILD_ID` names a server).
- A shared post that comes **with a permalink** is captured like a pasted link (the public page
  carries the location tag and author). One that comes **without one** (only a video file url)
  is saved by `SharedPostStore` under `data/ig_webhook/shared/` and served to the capture chain
  as its first rung (`capture_path = "dm_share"`): caption from the webhook, audio from the video.
  Its capture URL is a synthetic `instagram.com/spotbot-dm/<id>/` key, never a guessed shortcode.
- The store's misses don't count toward ADR-0006's circuit breaker: it has no account to flag.
- The instagrapi rung stays in the code but off (no `IG_USERNAME`), and is marked deprecated.

## Consequences

**Good:** capture runs on written permission; the burner and its session cookie go away; the
webhook app is small enough to review in one sitting; everything is tested offline
(`test_ig_webhook.py`).

**Bad:** without App Review, Meta delivers messages **only from people with a role on the app**
(team and invited testers); opening it to everyone needs App Review and Business Verification.
The webhook carries no location tag, GPS or tagged accounts (51% of the October pull had a tag),
so expect some venue/city accuracy loss. A quick tunnel's address changes every run. The exact
payload keys are documented in prose only; the parser accepts the likely names and every delivery
is logged (ids hashed) so the first real one becomes the fixture.

**Follow-ups:** record a real `ig_reel` delivery and pin it as a fixture; measure accuracy with and
without the burner (handoff #6); Business Discovery for venue profiles (needs a Facebook Page);
read Meta's Platform Terms before using webhook captions in the training corpus; a fixed public
address on the staging box.
