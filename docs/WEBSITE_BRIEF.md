# SpotBot landing page: brief (parked for Phase 3)

Saved Oct 21, 2026, to build in Phase 3 (Oct 27 – Nov 14). Track C (Experience) owns it.
Decisions made: **night dot-grid globe** and **showcase pins marked as examples**.

## The constraint

Apple's and Google's photoreal 3D maps stream tiles from their servers (API key, billing),
and a claude.ai artifact can only load scripts from a few CDNs at runtime: no tiles, images
or JSON fetches. So the globe is built in the browser with three.js, and the world map data
(e.g. the `world-atlas` 110m TopoJSON from npm) is embedded in the page.

## The brief

**SpotBot: landing page.** A one-page site for a Discord bot that turns the Instagram and
TikTok reels friends share into places they actually go.

**Hero:** a full-width, real-time 3D Earth at night. Land is a fine glowing dot grid, with a
thin atmosphere rim. On load it spins once, then the camera flies to Southern California.
Arcs shoot from around the world into LA, like reels being shared, and **spot pins** rise
out of LA, Long Beach and Orange County. Each pin opens a mini spot card (venue, vibe,
3 votes → "Locked in"). Headline: **"Turn the reels your friends share into places you
actually go."**

**Below, in order:**
1. **How it works:** paste a reel → SpotBot reads it → a votable card → 3 votes → it's on
   the calendar.
2. **Built local-first:** a local AI model, no paid APIs, captions never leave the
   machine, each server's data private.
3. **Honest numbers:** measured results with their intervals, every number taken from the
   repo (`docs/BASELINE.md`, `docs/DATA_PLAN.md`), never rounded up.
4. **Meet the team:** the five tracks and their owners.
5. **A closing call to action.**

**Look:** dark, deep-ocean navy with **one** warm accent, Spotty's pink, for pins and arcs.
A bold display typeface and a clean body face. Works on phones (lighter globe, fewer dots)
and respects "reduce motion" (a still globe). Self-contained: no outside calls at runtime.

**Pins:** example venues, clearly labeled as examples. Real spots from the labeled corpus
stay out of a public page: they are other people's posts.

## To start it

Paste this file into a Claude Code session and say "build it as an artifact". Optional: a
custom name or tagline.
