# SpotBot client design: a phone app beside Discord

Status: **accepted** (ADR-0008). Owner: Nick. Written 2026-10-31. A working prototype ships
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
- **Connect Instagram** (§8a): the guided data download, read on the phone, links only.
- Bot command **`/app`** sends the personal link, visible only to you.
- API: `POST /api/events/{id}/rsvp`, `POST /api/events/{id}/poll`,
  `POST /api/events/{id}/poll/vote`, `POST /api/invite`, `POST`/`GET /api/import`;
  `/api/match` (create, read, steps a / b / finish);
  `GET /api/events` now returns `rsvps`, `poll` and `geo_confidence`.

iPhone note: iOS has no share target for web apps; the native app (§9) adds it.

## 8a. Getting reels in without copy and paste

What a person wants: saving costs one tap, the app is useful before any setup, and
spots are found the way they're remembered (the video, who sent it). So:

1. **First run opens "Get your reels in"**, not an empty list. Two ways in:
   - **From now on: share.** Share → SpotBot (Android share target today; the native app
     adds iPhone). Later, DMs to the SpotBot account (ADR-0007, needs Meta App Review).
   - **The past: your data download.** The app opens Instagram's download page and shows
     the four choices people get wrong: only *Messages* and *Saved*, *All time*, *JSON*,
     *Low* media. Instagram emails the file (minutes to a day or two).
2. **The file is read on the phone.** The page unzips it itself (DecompressionStream, no
   library), walks the JSON, and keeps reel links with who sent them and when. The name
   that appears in every conversation is you, so your own shares aren't "from a friend".
   Captions are checked for food words and food-account handles: "1,240 reels from
   38 friends · 214 look like food". Only the links go to `POST /api/import`; message
   text and names stay on the phone, where cards read "💬 Maya sent this".
3. **The server drips them in.** `serving/imports.py` keeps waiting links per server
   (canonical `instagram.com/reel/<code>/` only, so nothing else can be captured), skips
   ones already imported, and hands 3 at a time to the capture queue only while it is
   idle, so a live paste always goes first. `/api/forget` drops the backlog.
4. **"Is this the right place?"**: spots with low geo confidence or no venue show one at a
   time at the top of My Spots: Yes · Not a place · Fix it.
5. **Paste a link** stays, as a small button.

Next: the Tonight button (3 picks near you), the DM webhook with one-tap account
linking (`ig.me/m/<account>?ref=<code>`, to verify), and a 5-person usability test
(save a reel, find the dumpling place a friend sent, pick dinner with two friends;
targets: first card within 60 s, a plan within 2 min).

## 8b. Match: what you have in common, privately

The social problem isn't saving spots, it's agreeing on one, and sharing a whole list
exposes habits, dates and who you were with. **Friends → Start a match** sends one friend
a link; both phones then see only what they both have: places they're both into (going,
maybe, voted, or sent to them) and reels that are in both their Instagram imports
(including the ones they sent each other), labelled from each phone's own data ("Maya
sent you this"). Nothing else on either list is revealed, to the friend or to SpotBot.

How: private set intersection on P-256 (`serving/psi.py`, the same math in the page).
Each phone hashes its items to curve points and multiplies by a secret only it holds;
the server's match room (`serving/match.py`, `/api/match…`) relays blinded points in
three one-time steps; because the blinding commutes, shared items line up and nothing
else can be read. Each side learns the overlap and the other's list size. First real
run: `docs/results/2026-11-02-match-run.md`.

## 8c. Design rules (no "AI slop")

Every surface follows these: the 3D site, the reference page, the phone app, the admin
pages and the Discord cards. Check a change against the list before it ships.

| # | Rule | What it means here |
|---|---|---|
| 1 | Delete filler badges | No decorative badge rows. Badges carry state ("NEW" card, "Leading" time) or go. |
| 2 | Drop redundant labels | No section eyebrows the nav already names; no field label repeating its value. |
| 3 | No random italics | Category names are plain text, not *italic*. |
| 4 | Mono only where needed | Monospace for commands, URLs and code only. |
| 5 | Use a design system | Each surface keeps its own colour tokens (`:root` variables); no one-off colours inline. |
| 6 | Show the real product | Screenshots of real cards, not "coming soon" boxes. |
| 7 | Enlarge the main feature | The spot (place name) is the biggest thing on every card. |
| 8 | Group with spacing | Spacing groups things; fewer boxes inside boxes. |
| 9 | Specific headlines | "Right 3 times in 4", not "Measured". "2 spots that match", not "Here's what I found". |
| 10 | Emphasize the CTA | One filled button per screen; everything else secondary. |
| 11 | One accent colour | Solid pink in the app and site, blue on admin pages. No gradient-filled text or buttons. |
| 12 | Consistent icons | One icon set per surface (the app's tab bar is one line-icon set, not mixed emoji). |
| 13 | Remove embellishments | No emoji glued to button text, no glow orbs, no decorative prefixes like 📝 or ✨. |
| 14 | Inner radius = outer − padding | Nested rounded corners line up. |
| 15 | Text contrast | Grey text at least 4.5:1 on its background (checked: site 6.6, app 5.6, admin 5.4). |
| 16 | Wrap headlines properly | `text-wrap: balance` on headings, `pretty` on paragraphs. |
| 17 | Align by eye | Optical alignment beats pixel-exact when they disagree. |
| 18 | Tabular numbers | Counts, votes, times and percentages use `tabular-nums`, with the same decimals side by side. |
| 19 | Loading states | Never a blank screen or "undefined": say what's loading. |
| 20 | Animate useful feedback | Motion only confirms an action (button press, toast, progress); none on reduced motion. |

What changed when these were applied (Nov 2026):

- **3D site:** removed seven section eyebrows; headline words use one solid accent instead of
  gradient text; the vague "Measured." became "Right 3 times in 4."; the test count is current
  (1,342) and both accuracy bars show one decimal; grey text contrast raised; flat icon tiles.
- **Reference page:** the empty "Demo video coming soon" box shows a real spot card until the
  video exists.
- **Phone app:** one solid pink for the main button; the tab bar uses one line-icon set and is
  solid; no emoji glued to button and chip text; a loading state while spots load; numbers are
  tabular; press feedback on buttons (off with reduced motion).
- **Admin pages:** the catalog is called SpotBot (not "SocialAgent — Event Catalog") and never
  shows "undefined events"; the dashboard's hard-coded badge row is one plain line; the share
  page has a real "Get SpotBot for your server" button.
- **Discord:** spot card footers say "Food & drink", not "food drink"; no 📝 before the summary;
  search results lead with "2 spots that match", drop the ✨, the italics and the "time TBD" line.

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
- Scope: the SRS lists other chat platforms as out of scope; that line changes now that
  ADR-0008 is accepted.
