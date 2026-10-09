# Proposal to Track A: move capture onto Instagram's permitted routes

From Nick (Platform), 2026-10-09. A proposal, not a merged decision: Track A owns
capture (`sources/`, `browser/`), so you decide the shape. If adopted, it gets an ADR.

## What happened

- 2026-10-08: we pulled 200 reels for the label corpus with `scripts/pull_dm_reels.py`
  (reads the burner's DMs) and `scripts/seed_corpus.py` (fetches each post), both through
  `instagrapi`, an unofficial client that imitates the Instagram app.
- 2026-10-09: Instagram auto-flagged the burner; an automated check then restored it
  ("follows Community Standards").

## Why this matters even though the account came back

Instagram has two rulebooks. **Community Standards** cover what an account posts; the
burner passed that check. The **Terms of Use** cover how Instagram is used, and they
forbid collecting data by automated means without permission. Nobody reviews that on
request; it's enforced by detection, which is what flagged us. So:

- Slower or smaller pulls make a flag less likely; they don't make the method permitted.
- A second flag is more likely to stick, and the authed source is rung 1 of the bot's
  capture chain (`pipeline/orchestrator.py`). Losing the account silently degrades every card.
- For a graded project with a security track, "we relied on a method the platform
  prohibits" is a finding against us. Written permission (an official API) is the fix.

## What exists today

`orchestrator.py` already falls back: authed (`sources/ig_authed.py`) → public page in
Chromium → `/embed/` page (`browser/ig_embed.py`, ~70% caption recovery). The authed
rung only turns on when `IG_USERNAME`/`IG_PASSWORD` are set (`serving/admin.py`).

## Proposal

| # | Change | Why |
|---|---|---|
| 1 | **Corpus links from Instagram's own data export** instead of `pull_dm_reels.py`. Done: `scripts/links_from_export.py` (+ `test_links_from_export.py`) reads the "Download your information → Messages (JSON)" export and prints the reel links. | Official copy of our own data; touches Instagram not at all. Same output as `pull_dm_reels.py`. |
| 2 | **Add an official oEmbed rung** (Meta's Instagram oEmbed API: free Meta developer app + app review) as rung 1, ahead of the public page and embed page. | The permitted way to fetch one public post when a user pastes it. Returns the embed HTML (usually with the caption) and the author. |
| 3 | **Retire the authed rung for bulk work.** Keep it only behind an explicit flag, at one reel per user paste, until #2 ships. | It's the source of the flag. |
| 4 | **Make a mid-run flag stop the authed rung** (`ig_authed.py`): today only a failed *first* login sets `_login_failed`; a `ChallengeRequired` / `LoginRequired` after a good login is retried on every URL. Treat those like a failed login, with a test. | Retrying a flagged account per URL is what turns a flag into a ban. |
| 5 | **"Add it manually" as the last rung.** When every rung fails, the bot asks the user for the place name. | Your #2 already lists it; the flag shows why it's needed. |
| 6 | **Measure before/after** with your Phase 1 harness: pass rate and venue accuracy with authed vs oEmbed + public only. | Your #2 "done when" asks for the before/after number. |

For a larger research dataset, the permitted route is Meta's **Content Library**
(research access through the university), not more burner pulls.

## What we lose without the authed rung, and what to watch

From the 200-reel pull: 51% had an IG location tag (with GPS) and tagged accounts were
available. The public/oEmbed paths mostly don't carry those, so expect some city and venue
accuracy loss. #6 measures it; the location/GPS gap is the main thing to report.

## Owners

- **Track A (you):** #2–#6, the fetch interface change, the harness numbers.
- **Nick:** the ADR, `.env`/RUNBOOK (Meta app credentials are secrets: never in git or logs), threat-model entry.
- **Track D:** review #2's credential handling and add "capture account banned" to `docs/THREAT_MODEL.md`.
