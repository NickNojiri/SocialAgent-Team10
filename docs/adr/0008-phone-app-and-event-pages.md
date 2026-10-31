# ADR-0008: A phone app and invite-link event pages beside Discord

- **Status:** proposed (needs the team's review and the professor's OK on scope)
- **Decided:** — · **Recorded:** 2026-10-31 · **Owner:** Nick
- **Related:** ADR-0003 (Discord-first, chat-agnostic backend), ADR-0007 (Instagram DM
  webhook), `docs/CLIENT_DESIGN.md`, `docs/THREAT_MODEL.md` T2

## Context

ADR-0003 kept Discord as the only client "until the loop is proven" and the backend
chat-agnostic so other surfaces could be added as siblings. The loop works. People plan on
their phones, reels arrive in Instagram, and friends outside a server can't take part.

## Options considered

1. **Stay Discord-only.** No new surface to maintain, but no personal view and no way in
   for friends outside the server.
2. **Replace Discord with an app.** Throws away the group features and the place where
   the paste already happens.
3. **Add a phone app and event pages beside Discord, on the same API** (chosen). Each
   surface has its own job: Discord for the group, the app for the person, the event page
   for everyone else.
4. **Native app first.** Best phone experience, but nothing to show this semester.

## Decision

- Ship a **web app (PWA) at `/app`** now, served by the admin service: My Spots, add by
  link or Android share sheet, event pages with Going / Maybe / Can't, a "find a time"
  poll, and invite links. A native Expo app follows in the spring.
- Add a **guest token scope** (`g`): read one catalog, RSVP, vote in its poll. A member's
  `/app` link holds a guest token and a token signed for that user; neither can delete,
  forget or change settings. Tokens travel in the URL fragment.
- Store RSVPs and polls in the spot's metadata, capped, so `/privacy` deletion covers them.
- **Discord stays the group surface** and gets group features first.
- Instagram input stays within its terms: share sheet, the user's own data export, and the
  official DM webhook. No logged-in scraping.

## Consequences

- Two more surfaces to keep working; mitigated by one API and the authz suite
  (69 attacks refused, 32 legitimate requests accepted, including the new routes).
- Invite links are bearer capabilities (read + RSVP under any name, capped at 200 per spot);
  rotating `SPOTBOT_SIGNING_KEY` revokes them.
- The SRS's "no other chat platforms" line changes to "Discord for groups; a phone app and
  event pages for people and guests" once this is accepted.
