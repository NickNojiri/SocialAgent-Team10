# SpotBot Product Plan — from team project to a product people actually use

## Context

SpotBot today (branch `claude/recent-branch-review-0t0r9y`) is a working Discord bot: paste an Instagram reel and it becomes a votable "spot card" — caption + spoken audio transcribed locally (Whisper), venue/category/time extracted by a local LLM (Ollama), geocoded via OpenStreetMap, stored in ChromaDB. `/plan` reads recent chat and proposes an outing. A dark-mode web admin (`src/ingestion/serving/admin.py`) shares the same catalog.

**The core insight:** group chats are where "we should go here!" dies. Every friend group has a graveyard of shared reels nobody acted on. SpotBot converts that existing behavior (already happening, zero new habit needed) into a group memory and a plan. That's a real wedge — capture requires no behavior change, and the payoff (actually going out) is emotional.

**Direction decided with Nick:** hosted bot with one-click Discord invite + OSS core for self-hosters · deepen Discord first, then web companion + more capture sources, other chat apps later · free while growing (paywall lines sketched, nothing gated yet).

**The one product-killing constraint:** unauthenticated IG capture succeeds only ~40–70% of the time (measured ceiling, `docs/IG_AUTH_INGESTION_PLAN.md`). A product that fails a third of first-time pastes cannot retain anyone. Reliability is Phase 0, before any growth work.

---

## North star

> **Paste a reel → votable card in under 15 seconds, ≥95% of the time.**
> Metric that matters: **% of captured spots that become attended plans.**

---

## Phase 0 — Foundation (reliability + hostability). *Nothing else matters until this works.*

### 0.1 Capture reliability → 95–99%
- Execute `docs/IG_AUTH_INGESTION_PLAN.md` as written: `AuthedInstagramSource` (instagrapi + burner session) feeding the existing `RawPostSnapshot` → pipeline path; paid resolver (Apify/RapidAPI, ~$30–50/mo) as the drop-in fallback with the same module boundary.
- Measure the live pass rate with the 13 real URLs already in `test_ig_live.py` + the 6 from `docs/REEL_TRANSCRIBE_TEST_PLAN.md`. Publish the number; it's the quality bar for every release.
- Instrument capture success rate as a first-class metric (log per-stage outcomes in `RunReport` — already structured for this in `pipeline/orchestrator.py`).

### 0.2 Multi-tenancy (required for hosted)
Current state is single-group: one shared Chroma collection (`event_inspirations`), `channels.json` on disk, `DROP_CHANNELS`/`SUGGESTION_CHANNELS` in memory. For a hosted bot every Discord server needs its own catalog.
- Scope everything by `guild_id`: Chroma collection per guild (or one collection with a `guild_id` metadata filter — simpler, Chroma supports `where`), and move channel config from `channels.json` into the existing `db` service (SQLite → Postgres when hosted).
- `IngestionPipeline` and `RecommendationService` take a `guild_id`; `admin.py` endpoints become `/api/guilds/{gid}/events/...`.

### 0.3 Hosted architecture
- **Job queue for ingestion.** Today the bot does a synchronous `POST /api/ingest` with a 180s timeout (`app/bot.py::handle_reel_capture`). Hosted, that's a thundering-herd risk. Replace with enqueue → worker pool → bot edits its "⏳" message when done. (Redis + a small worker, or even Postgres `SELECT FOR UPDATE SKIP LOCKED` to stay lean.)
- **Hosted LLM decision:** self-hosters keep Ollama (the privacy story stays true in OSS). The hosted tier runs a small hosted model or a pooled GPU box running the same Ollama models — the `llm_extractor` already routes by model name, so this is config, not code.
- Whisper transcription runs server-side on the hosted tier (it's optional/degradable already — `transcriber.py` is injectable).
- One-click invite URL + a `/setup` wizard (see 1.1). Terms of service + privacy policy pages (capture is user-initiated paste only — never crawling; that's both the ethics line and the ToS defense).

---

## Phase 1 — Discord UX excellence (make the core loop delightful)

### 1.1 Onboarding that sells itself
- `/setup` wizard: pick a drop channel (or create `#spot-drops`), set the group's **home city** (powers distance/"near you"), done in 30 seconds.
- First-capture magic moment: the bot's very first card in a server includes a one-line "psst — anyone can 👍 this; try `/plan` when you have a few spots."
- When a capture fails: empathetic error + **manual-add modal** (Discord modal form: venue, vibe, link) so the user never hits a dead end. Failure text exists (`_capture_failure_text`); the modal is new.

### 1.2 Spot card v2
- **Thumbnail from the reel** — `og:image` is already collected by `SocialSessionManager._collect_meta`; persist it into Chroma metadata and `embed.set_thumbnail(...)`. Cards go from text-only to instantly scannable.
- **Map link** (`https://maps.google/?q=lat,lng` from the geo enricher's coords) + distance from home city.
- **Who's in:** show voter names/avatars on the card ("👍 3 — nick, sam, jo"), not just a count. Requires storing voter ids with votes (currently just an int in metadata) — this is also what makes quorum (1.3) possible.
- Live capture progress: edit one status message through the stages (fetched → transcribed → cataloged) instead of a silent 20–30s wait.

### 1.3 `/plan` v2 — close the loop to a real event
This is the retention feature. Today `/plan` posts picks; nothing happens after.
- **Quorum:** when N people tap "Want to go" on a plan pick, the bot announces "That's 4 of you — locking it in?" with a date picker (buttons for next few evenings, mini-Doodle style).
- **Auto-create a Discord Scheduled Event** on lock-in — the `guild.create_scheduled_event` code already exists in the old event-planner bot (`main` branch `app/bot.py::handle_create_event`); port it.
- Reminder ping day-of + a `.ics` / Google Calendar link on the event card.
- **The went-there loop:** the day after the event, ask "did you go? 🌟 rate it" — closes the data loop, powers recommendations (2.2), and is the north-star metric's numerator.

### 1.4 Ambient value (reasons to keep the bot around)
- **Weekly digest** to the drop channel: "Your crew saved 7 spots this week · top pick: 🍜 Menya Hanabi (5 👍) · 3 spots still unplanned" — `src/ingestion/exporters/markdown_digest.py` already exists; wire it to a scheduler.
- `/catalog` v2: filter by category/area/scheduled (`/catalog nightlife`), pagination.

---

## Phase 2 — Capture everywhere + the web surface

### 2.1 More capture sources (in priority order)
1. **TikTok** — the other half of "food reel" culture; same og:-tag + embedded-JSON patterns the IG extractor uses (`extractors/instagram.py` is the template; `extractors/base.select_extractor` already routes by domain).
2. **YouTube Shorts** and plain URLs (blogs, Eater lists, Google Maps share links — `extractors/generic.py` already handles JSON-LD pages).
3. **Screenshots** — OCR (tesseract or vision LLM) for "my friend texted me this" images pasted into Discord.

### 2.2 Taste-aware suggestions
- The "Add suggestions" button (just shipped) currently does similarity search. Evolve to a per-guild **taste profile**: weight by votes and *attended* events (1.3's went-there data), decay stale spots, diversify categories.
- "More like the places we actually went" is a moat — it compounds with usage and can't be copied without the data.

### 2.3 Web companion (evolve `admin.py`, don't rebuild)
- **Shareable read-only catalog page per guild** — `spotbot.app/g/<invite-code>`: the group's map (spots plotted, OSM tiles), filterable list, vote counts. **This is the growth surface** — it's viewable by people who don't have the bot yet, with an "Add SpotBot to your Discord" button.
- Map view + category filters on the existing admin page for members.
- Later: web-side capture (paste a link on the page — `POST /api/ingest` already exists).

---

## Phase 3 — Growth engine (free-while-growing playbook)

- **Built-in viral loop:** every shared catalog page and exported digest footer says "Powered by SpotBot — add it to your Discord." The product is used *in groups*, so every activated server exposes ~5–50 new people by default. Optimize server→server spread, not individual signups.
- **Distribution channels:** Discord App Directory listing (the big one — browsable install surface), top.gg + discord.bots.gg, Product Hunt launch, r/Discord + food-city subreddits.
- **The demo GIF** (paste reel → card in 10s) at the top of the README and landing page — the aha moment is visual; lead with it. A public demo server anyone can join and try instantly.
- **Niche-first:** food-obsessed friend groups and college-city servers (the Long Beach roots are the seed). Nail one city's crews before generalizing; "the bot every LB friend group has" beats "a bot anyone could use."
- **Starter packs** to kill the cold start: on `/setup`, offer to seed the catalog with 10 curated spots for the group's city (built from your own aggregate data or hand-curated) so `/events` and "Add suggestions" work on day one.
- **OSS as a channel:** the GitHub repo (README already product-grade) attracts self-hosters and contributors; hosted is the convenience upsell. Tag releases, add a good-first-issue board.
- Future paywall lines (do NOT gate yet, just keep them cheap to add): captures/month per server, catalog size, taste-profile recommendations, multi-city support, priority ingestion queue.

### Other chat apps (after Discord is proven)
Telegram first (bot API is closest to Discord's, same card/button model maps cleanly), then WhatsApp. The ingestion/recommend backend is already chat-agnostic HTTP — only the `app/` layer is Discord-specific.

---

## Technical foundations checklist (supports everything above)

| Need | Today | Change |
|---|---|---|
| Multi-tenancy | single collection + `channels.json` | `guild_id` scoping, config in DB (0.2) |
| Ingestion at scale | sync HTTP, 180s timeout | job queue + workers (0.3) |
| Vote identity | int count in metadata | per-user vote records (1.2/1.3) |
| Images on cards | og:image collected, dropped | persist to metadata (1.2) |
| Reliability metric | RunReport exists, unlogged | emit + dashboard (0.1) |
| Legal | none | ToS/privacy, user-initiated-only stance (0.3) |

## Metrics that decide everything
- **Capture success rate** (target ≥95%) — quality bar
- **Time-to-card** (target <15s p50) — the magic-moment latency
- **Server D7/D30 retention** (did the server capture again next week?)
- **Spots → plans conversion** (north star), and **plans → attended** (went-there loop)
- Viral coefficient: invites generated per active server

## Risks
- **IG ToS / scraper breakage** — user-initiated paste only (never crawl); paid-API fallback behind the same module boundary; instagrapi breaks periodically, plan for it.
- **Hosted LLM cost** — start with pooled Ollama on one GPU box; usage-based ceiling per server; this is also the natural first paywall if costs bite.
- **Discord platform dependence** — mitigated by the chat-agnostic backend + web surface.
- **Cold start per server** — starter packs (Phase 3) + "Add suggestions" needs ≥~10 spots to feel smart.

## Recommended first slice (next ~2 weeks of build)
1. Authed IG source + **measured** live pass rate (0.1) — the go/no-go number
2. `guild_id` scoping of catalog + channel config (0.2)
3. Spot card v2: thumbnail + map link (1.2, small + high visible impact)
4. `/plan` quorum → Discord Scheduled Event (1.3, port existing code)
5. Public shareable catalog page (2.3, the growth surface)

## Validation
- Reliability: run the 19 known reel URLs through the authed path on a clean network; record pass rate in `docs/IG_AUTH_INGESTION_PLAN.md` (replacing estimates).
- Offline suite stays green (`pytest -k "not live"`, 143 passing) with new unit tests per feature (fake instagrapi client, guild-scoping tests, card-embed tests in `app/test_cards.py`).
- Dogfood: run the hosted stack for 2–3 real friend-group servers (the team's own) for two weeks; watch spots→plans conversion before any public launch.
- Launch gate: ≥95% capture, <15s p50 time-to-card, one full plan→attended cycle completed by a dogfood group.
