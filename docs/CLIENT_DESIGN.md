# SpotBot client design: a phone app beside Discord

Status: **proposed** (ADR-0008). Owner: Nick. Written 2026-10-31. A working prototype ships
with this doc: `/app` on the admin service (see "Prototype" below).

## 1. Problem

People plan on their phones. The reels arrive in Instagram DMs, nobody acts on them, and
SpotBot today only works inside a Discord server. Two gaps:

- **Personal:** "show me the restaurants my friends sent me, so I can pick one tonight."
- **Outside Discord:** friends who aren't in the server can't say "I'm in".

**Success:** a new user goes from link to their own restaurant list in under 10 minutes, and
a friend RSVPs from an invite link without an account. Nothing is sent to outside AI services.

## 2. Constraints

- Instagram's terms (see §3), local-first, no paid APIs (SRS C-1/C-2), every catalog call
  tenant-authorized (THREAT_MODEL T2), a four-person team with one semester left this year.
- Non-goals: reading anyone's DMs automatically, a cross-server social graph, SMS.

## 3. Getting reels from Instagram

| Path | Allowed? | Use |
|---|---|---|
| Log in as the user and read DMs with a script | **No** (the burner was flagged Oct 9) | never |
| Official API for personal accounts | **No** (only Business/Creator accounts now) | never |
| "Download your information" export | **Yes**: the user's own copy | one-time import of the backlog (`scripts/links_from_export.py` already parses it) |
| Share sheet (Instagram → Share → SpotBot) | **Yes**: user-initiated, one reel at a time | daily capture; the prototype's Android share target |
| DM the SpotBot account | **Yes**: official messaging webhook (ADR-0007) | capture from inside Instagram; heavy processing on our server |

The share sheet passes a link only. Reading the caption still needs the public reel page
(our embed-page fetch, `ig_embed.py`), or the video file if the user shares that.

## 4. Where processing runs (hybrid)

| Step | On the phone (later) | Today |
|---|---|---|
| Receive the reel | share sheet / share extension | `/app/share` (Android PWA) |
| On-screen text | Apple Vision / ML Kit | RapidOCR on the host |
| Audio | whisper.cpp (tiny/base) | faster-whisper on the host |
| Pick the venue | ported rules + int8 M2 (~100 MB, optional download) | rules (M2 in research) |
| Similar spots | small on-device embedder | `mxbai-embed-large` via Ollama |
| Fetch the reel page | not possible (no headless browser) | Playwright on the host |

Rule for the port: run `fixtures/labels.jsonl` through both the Python and phone versions
in CI and require the same answers.

## 5. Surfaces, each with its own job (Discord stays relevant)

| Surface | Job |
|---|---|
| Discord bot | **the group**: voting, lock-in, threads, reminders, `/plan` reading the chat |
| Phone app | **you**: your spots, restaurants filter, add from the share sheet, your plans |
| Event page (invite link) | **everyone else**: RSVP with just a name, vote in the time poll |
| DM to the SpotBot account | capture without leaving Instagram |

They feed each other: a member's "Going" in the app also moves their Discord vote; lock-in
produces an event page; the event page ends with "Add SpotBot to your server". Group
features ship in Discord first, so it stays the best place to plan together. Watch the
`/dash` counts per surface (where pastes come from, where nights out happen) before moving
effort.

## 6. Borrowed from Partiful

| Idea | In SpotBot | Status |
|---|---|---|
| Guests need no account | invite link → RSVP with a typed name | **prototype** |
| Soft commitment | Going / Maybe / Can't | **prototype** (app); Discord buttons next |
| Show who's coming | "Maya + 2 others are in" up front | **prototype** |
| Details can be TBD | "Find a time" poll instead of next Friday 7 pm | **prototype** |
| The invite is the vibe | poster from the reel's image, tinted by category | **prototype** |
| Reminders and host blasts | outing thread + day-before reminder | next (Discord) |
| The party continues | "We went!" + one photo or rating → recap | next |
| Every guest is a future host | "Add SpotBot to your server" on every page | exists on `/share` |
| Not taken | phone numbers/SMS (personal data, paid), cross-server friend graph (T2) | — |

## 7. Architecture

- **Same backend, more adapters** (ADR-0003): nothing in `src/` knows which surface called.
- **Tokens:** a new **guest** scope (`g`) reads one catalog and may RSVP or vote in a poll,
  nothing else. A member's `/app` link carries a guest token plus a token signed for that
  user (add reels, vote, start polls, invite as themselves). Neither can delete, forget or
  change settings. Tokens ride in the URL fragment, which browsers never send to a server.
- **Data:** RSVPs and the poll live in the spot's metadata (`rsvps`, `poll`, JSON strings,
  like `voters`), so `/privacy`'s delete already removes them. Caps: 200 RSVPs per spot,
  2–5 poll times, 40-character names.
- **Personal catalog:** a DM stash (`dm-<user id>`) is already a per-person tenant; `/app`
  run in a DM opens it.

## 8. Prototype (shipped with this doc)

`GET /app`: an installable web app (PWA) served by the admin service.

- **My Spots** with Restaurants / Everything / Music / Nightlife filters, and add by link.
- **Share target:** installed on Android, SpotBot appears in Instagram's share sheet;
  `/app/share` hands the link to the page.
- **Event page:** poster, map and reel links, Going / Maybe / Can't, who's coming, a
  "find a time" poll, and **Invite friends** (a guest link; no Discord needed).
- **Plans** (what you're in on, with the leading time) and **You** (name, privacy).
- **Demo mode** with example spots when opened without a link.
- Bot command **`/app`** sends the personal link, visible only to you.
- API: `POST /api/events/{id}/rsvp`, `POST /api/events/{id}/poll`,
  `POST /api/events/{id}/poll/vote`, `POST /api/invite`; `GET /api/events` now returns
  `rsvps` and `poll`.

iPhone note: iOS has no share target for web apps; the native app (§9) adds it.

## 9. Roadmap

1. **Phase 4 (to Dec 11):** this prototype; Going/Maybe/Can't buttons on Discord cards;
   the poll in Discord at lock-in; usability test with the `/survey` SUS.
2. **Spring, early:** Expo (React Native) app: share extension, on-device OCR and rules,
   local list synced to the same API; the export import on the phone.
3. **Spring, later:** on-device Whisper and M2 for a fully private mode.

## 10. Risks

- Installing Python, Chromium and Ollama on the host stays the hard part; the phone app
  needs a reachable host (home PC through a tunnel, or the Oracle VM).
- Instagram can wall the embed page; the user then adds the spot by hand.
- An invite link is a bearer capability: anyone holding it can read that catalog and RSVP
  under any name (capped). Rotating the signing key revokes every link.
- Store accounts cost money (Apple $99/yr, Google $25 once); class demos can use Expo Go
  and Android sideloading.
- Scope: the SRS lists other chat platforms as out of scope; ADR-0008 needs the
  professor's OK before it counts toward deliverables.
