# Plan: capture reels through Instagram's official API

> **Status 2026-10-24: built** (ADR-0007). Webhook app `serving/ig_webhook_app.py`, parser and
> store `sources/ig_webhook.py`, `scripts/run_instagram.ps1`, `scripts/check_instagram.py`,
> `test_ig_webhook.py`, RUNBOOK "Instagram" section, THREAT_MODEL T10, `docs/PRIVACY.md`.
> Left: the account setup below, and pinning a real `ig_reel` delivery as the test fixture.

From Nick (Platform), 2026-10-24. Follows up `TRACK-A-COMPLIANT-CAPTURE.md` (2026-10-09), which
proposed this after the burner's first flag and was never adopted. Track A owns `sources/`, so
the source module below is a proposal to them; the webhook route and secrets are Nick's.

## Why now

The burner (instagrapi, logged in as a person) was challenged on 2026-10-23 and **suspended** on
2026-10-24 (appeal pending). Logged-in automation breaks Instagram's Terms of Use however slowly
it runs, so another burner would end the same way. The permitted route: SpotBot gets its own
**Instagram professional account**, and people DM reels to it. Meta then sends us each shared reel.

## What the official API gives us (checked against Meta's docs, 2026-10-24)

| Need | Official route | Notes |
|---|---|---|
| A reel someone shares with SpotBot | **Instagram Messaging API**, `messages` webhook | A shared reel arrives as an attachment of type `ig_reel` (or `reel`) whose payload has the reel's **title (caption), url and video id** (changelog, 2024-06-11). Post shares arrive as `ig_post` (media id, url, title). Unknown until we see a live event: whether `url` is a playable video link (for Whisper) or only a permalink. |
| Login type | **Instagram API with Instagram Login** | Permissions `instagram_business_basic` + `instagram_business_manage_messages`. No Facebook Page needed for messaging. |
| Venue profiles (business vs. creator, bio, website) | **Business Discovery** | Only through *Facebook Login*: the IG account must be linked to a Facebook Page; permissions `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`. Business/Creator targets only; age-gated accounts are omitted. Optional second step. |
| One public post pasted in Discord | **Meta oEmbed Read** | Needs App Review. Since Nov 2025 it no longer returns author or thumbnail, so it adds little over our `/embed/` fetch. Low priority. |
| More corpus links | **"Download your information"** export | Already built: `scripts/links_from_export.py`. |

**Access limits that shape the demo:**
- **Without App Review, webhooks fire only for people with a role on the app** (admins, developers, testers). The team plus a few invited testers is fine for the capstone. Opening it to everyone needs App Review **and Business Verification** (a legal business), which a student team may not get.
- The app must be **published (Live mode)** to receive webhooks, regardless of review status. That needs a privacy-policy URL; our GitHub Pages site can host one.
- Replies to a sender are allowed inside the standard messaging window (24 hours for most actions).
- Meta sends webhooks to a **public HTTPS URL**. For a local test, a free tunnel (e.g. `cloudflared`) can forward to `localhost:8010`. For always-on, use the Oracle staging box (paused on capacity).

**What we lose vs. the burner:** location tags with GPS (51% of the October pull had one), tagged
accounts, and comments. The webhook carries only url + caption. Measure the accuracy change with
the Track A harness before and after (handoff #6).

## Setup (Nick, by hand: accounts and secrets)

1. Create a **new** Instagram account for SpotBot (don't reuse the suspended burner) and switch it to **Professional** (Creator or Business) in its settings.
2. At developers.facebook.com, create an app (Business type), add the **Instagram** product, choose **API setup with Instagram login**, and connect the SpotBot account.
3. Add teammates and testers under **App roles**. Each accepts in the Instagram app (Settings → Website permissions → Apps and websites → Tester invites).
4. Generate the account's access token. Put `IG_APP_SECRET`, `IG_ACCESS_TOKEN` and a random `IG_WEBHOOK_VERIFY_TOKEN` in `.env` only: never in git, logs or chat.
5. Add a privacy-policy page and set the app to **Live**.
6. Webhooks → callback `https://<tunnel or staging host>/webhooks/instagram`, the verify token, and subscribe to **`messages`**.
7. (Later) Link the account to a Facebook Page and add Business Discovery.

## Code (built offline against recorded payloads)

| Piece | Where | What |
|---|---|---|
| Webhook route | `serving/admin.py` (Nick) | `GET /webhooks/instagram` answers Meta's `hub.challenge` when `hub.verify_token` matches. `POST` **rejects any body whose `X-Hub-Signature-256` HMAC (app secret) doesn't match**, then hands the parsed events to the capture queue and returns 200 fast. |
| Event parser | `sources/ig_webhook.py` (Track A) | Webhook JSON → one item per `ig_reel`/`reel`/`ig_post` attachment: url, title as caption, media/video id, sender IGSID. Ignores echoes, reactions and `is_unsupported`. Pure function, no network. |
| Capture | `serving/jobs.py` | Reuses the existing paste-a-link job: the caption comes from the webhook, the rest from the public page/embed fetch. If the payload url turns out to be a video link, feed it to the transcriber directly. |
| Reply (optional) | small Send API client | "Saved: <venue>" back to the sender, inside the 24-hour window. |
| Retire instagrapi | `sources/ig_authed.py`, `admin.py` | Leave it off by default (it already is without `IG_USERNAME`). Remove it from docs as a recommended path. |
| Tests | `test_ig_webhook.py` | Signature good/bad/missing, verify-token handshake, `ig_reel` and `ig_post` parsing, unsupported and echo events ignored, oversized body refused. |
| Threat model | `docs/THREAT_MODEL.md` | New public endpoint: signature check mandatory, body size cap, rate limit, never log tokens or the raw payload's sender ids. Plus "capture account banned" as a risk. |
| ADR | `docs/adr/` | "Capture through the official Instagram Messaging API", superseding the authed rung of ADR-0006. |

Before relying on the parser, record one real `ig_reel` event from the tester account and save it
(with ids scrubbed) as the test fixture. Meta's docs describe the payload fields in prose only.

## Check Meta's Platform Terms

Before reusing webhook captions in the **label corpus** or for model training, read Meta's Platform
Terms on storing and using Platform Data. Serving cards to the people who sent the reels is the
intended use; building a training dataset may need a separate look.

## Sources

- Instagram Messaging API (Instagram Login): developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api
- Instagram Messaging webhooks: developers.facebook.com/documentation/business-messaging/instagram-messaging/webhooks
- Instagram Messaging changelog (`ig_reel` 2024-06-11, `ig_post` 2025-10-30, `share` removed 2026-04-27): developers.facebook.com/documentation/business-messaging/instagram-messaging/changelog/
- Business Discovery: developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery
- oEmbed field removals: developers.facebook.com/blog/post/2025/04/08/oembed-updates/
