# SpotBot — Team Priority TODO

Last updated 2026-09-10. Owner: Nick. One prioritized list for the whole team.

This is the "what do I pick up next" list. It rolls up `docs/PRODUCT_ROADMAP.md`
(the why), `docs/MILESTONES.md` (the 100 Nights challenge), `docs/NEXT_SESSION.md`
(the build queue) and `docs/EXTRACTION_ACCURACY.md` / `docs/HANDOFF.md` (the label
loop, on the `extraction-label-loop` branch) into a single ranked backlog.

**North star:** paste a reel → votable card in <15s, ≥95% of the time. The metric
that actually counts is **captured spots that become attended outings** (the
"100 Nights Out" challenge).

**The two hard constraints everything is judged against:**
1. **Zero-Dollar Stack** — marginal cost per server stays at $0 (local/pooled
   Ollama, OpenStreetMap, free-tier hosting). No paid Places/vision API.
2. **Text stays local** — post text never leaves infrastructure we control
   (this is the line the hosted-Haiku experiment has to argue against).

Priority tiers: **P0** = nothing above it ships until this is solid · **P1** =
the retention loop · **P2** = reach · **P3** = growth. Within a tier, top = do first.

---

## P0 — Foundation: capture works, and we can prove what it extracts

### P0.1 · Capture reliability → 95–99%  *(roadmap 0.1)*
The product-killer. Unauthenticated IG capture succeeds only ~40–70% of the time
(measured ceiling). A product that fails a third of first-time pastes retains
nobody.
- Execute `docs/IG_AUTH_INGESTION_PLAN.md`: `AuthedInstagramSource` (instagrapi +
  burner session) into the existing `RawPostSnapshot` → pipeline path.
- Paid resolver (Apify/RapidAPI) as a drop-in fallback behind the *same* module
  boundary — only switched on if the free path drops below 90% measured.
- Instrument capture success as a first-class metric: log per-stage outcomes in
  `RunReport` (`pipeline/orchestrator.py` is already structured for it).
- Publish the live pass rate from the 19 known reel URLs
  (`test_ig_live.py` + `docs/REEL_TRANSCRIBE_TEST_PLAN.md`). That number is the
  quality bar for every release.
- Carry-over: authed IG login currently blocked (flagged burner + blacklisted
  IP). Retry needs a fresh burner + warm-up + a different network.

### P0.2 · Extraction accuracy — the label loop  *(branch `extraction-label-loop`; `docs/EXTRACTION_ACCURACY.md`)*
Capture runs end to end but the *fields it fills are often wrong*. This is the
current active workstream. See the "Why labeling matters" section below.

Honest held-out numbers today (`python -m src.ingestion.eval --offline --split test`):

| metric | held-out |
|---|---|
| venue exact | ~50% |
| category | ~63% |
| city | ~73% |
| promo-rejection (is_vague) | ~24% |

Round-6 targets, in priority order:
1. **Category → ~78%.** Keyword matching (`_CATEGORY_KEYWORDS`) has plateaued.
   Replace with an embedding-nearest classifier: embed the caption with the
   local `mxbai-embed-large`, compare to per-category centroids built from the
   labeled corpus, pick nearest. In-house, no new dependency.
2. **Promo-rejection → up from 24%.** `looks_vague()` can't tell a no-name food
   post from a real place. Genuinely an LLM job — defer to the Haiku experiment
   (P0.4).
3. **Venue → ~55%.** In-house headroom is small; grow `fixtures/venue_aliases.json`
   from review corrections (`scripts/build_aliases.py`). Past ~55% needs an LLM.
4. **Gatekept reels** (venue only in comments): add a comment-scrape step to the
   capture path (`instagrapi` top ~10 comments). New ladder:
   caption → transcript → OCR → comments → give up. Distinguish "no-name place"
   (ask the user) from "not a place" (reject).

Rules of the road for any parser change:
- Run `eval.py --split test` before *and* after. Tune on `train`, report `test`.
- After any `fixtures/labels.jsonl` change: `python scripts/build_aliases.py`,
  then bump the floors in `test_extraction_labels.py` to the new `--split all`
  numerators (CI ratchet).

### P0.3 · `/dash` Review tab — the loop that scales the corpus  *(EXTRACTION_ACCURACY slice 2)*
Right now labels are grown by hand-running `scripts/review.py`. To grow past a
few hundred rows the corpus has to fill itself from real traffic:
- Capture also writes `predicted` + the exact input it saw into the row.
- `/dash` gets a **Review tab**: a human marks each field
  `right`/`wrong`/`missing` and types the correction; the row appends to
  `fixtures/labels.jsonl`.
- Corrections feed back automatically: alias table, few-shot bank, rule mining.
- Also: finish the 16 `needs_review` rows; keep growing the corpus (bigger
  held-out set = firmer numbers, wider city gazetteer).

### P0.4 · The Haiku experiment — go/no-go on a cheap hosted LLM  *(do after any more labeling)*
The clean test of "does a cheap LLM beat the honest ~50% venue / ~63% category?"
- `scripts/label_with_haiku.py` exists. Point it at the corpus; compare its
  venue/city/category/in_catalog to gold on `--split test`.
- Needs a **funded Anthropic API account** (`console.anthropic.com` + a card —
  Pro credits do NOT work for API). ~$1–2 for one pass over 417 rows.
- Decision it answers: ship heuristic-only, or heuristic + LLM-for-the-hard-rows.
- Caveat: sending caption text to a hosted API breaks the "text stays local"
  line. If adopted, likely shape = hosted tier + low-confidence captures only;
  self-hosters keep Ollama.
- Hardware note: no GPU on the box. Any Ollama chat model on CPU is too
  slow/weak for extraction (measured: 30–56s/call, *worse* accuracy). Ollama
  stays for embeddings only.

### P0.5 · Spam / budget protection — before any hosted LLM goes live  *(EXTRACTION_ACCURACY §"Protecting the extractor")*
A flood of pasted links = hundreds of pipeline runs / LLM calls = burned credits
+ a polluted catalog. All in-house, in priority order:
1. **Dedup before any work** — a reel already in the guild catalog
   (by `content_hash`) returns the existing record; no fetch, no LLM.
2. **Confidence gate on the LLM** — only captures below a confidence threshold
   (which slot filled the venue) ever reach the model.
3. **Global daily budget cap** — hard counter of N LLM calls/day; on hit →
   heuristic-only + log.
4. **Per-user + per-guild rate limits** — token bucket (e.g. 10/user/10min,
   60/guild/hour); over → friendly "slow down" reply.
5. **Lock the admin API** — it binds `0.0.0.0:8010`. Bind `127.0.0.1` + reach it
   from the container over the docker bridge, or add a shared-secret header.
6. **Discord-side raid guard** — ignore messages with > K links; optionally
   ignore links from accounts that joined < N minutes ago.

### P0.6 · Multi-tenancy — required for a hosted bot  *(roadmap 0.2)*
- Scope everything by `guild_id`: one Chroma collection with a `guild_id`
  metadata filter (`where`), channel config moved from `channels.json` into the
  `db` service.
- `IngestionPipeline` / `RecommendationService` take a `guild_id`; `admin.py`
  endpoints become `/api/guilds/{gid}/...`.

### P0.7 · Hosted architecture basics  *(roadmap 0.3)*
- **Job queue for ingestion.** Today the bot does a synchronous `POST /api/ingest`
  with a 180s timeout — a thundering-herd risk when hosted. Enqueue → worker pool
  → bot edits its "⏳" message when done.
- Pick the always-on box for the dogfood deploy (spare PC or cheapest VPS,
  `llama3.2:3b` profile).
- ToS + privacy-policy pages (capture is user-initiated paste only — never
  crawling; that's both the ethics line and the ToS defense).

### P0.8 · Backfill the poisoned catalog rows
Re-capture the ~12 rows saved with `venue_name = "Locations"` now that the
scraper fix is in. The ~5 whose venue is in the retained first line (Erewhon,
Tiendita, Waterfall Chicken, …) are fixed just by re-running extraction — no
re-fetch.

---

## P1 — Close the loop: capture → card → plan → "we went"

### P1.1 · `/plan` v2 — turn picks into a real event  *(roadmap 1.3; the retention feature)*
Today `/plan` posts picks and nothing happens after.
- **Quorum:** N people tap "Want to go" → bot posts "That's 4 of you — locking it
  in?" with a date picker (buttons for the next few evenings).
- **Auto-create a Discord Scheduled Event** on lock-in — port
  `guild.create_scheduled_event` from the old event-planner bot
  (`main` branch `app/bot.py::handle_create_event`).
- Day-of reminder ping + a `.ics` / Google Calendar link on the event card.
- **The went-there loop:** the day after, ask "did you go? 🌟 rate it". This is
  the numerator of the north-star metric and the counter "100 Nights" runs on.
  *(Deferred by Nick until real users — but it's the point of the whole product.)*

### P1.2 · Spot card v2  *(roadmap 1.2)*
- **Thumbnail from the reel** — `og:image` is already collected by
  `SocialSessionManager._collect_meta`; persist it into Chroma metadata and
  `embed.set_thumbnail(...)`.
- **Map link** (`https://maps.google/?q=lat,lng`) + distance from the group's
  home city.
- **Who's in:** show voter names/avatars ("👍 3 — nick, sam, jo"), not just a
  count. Requires storing voter ids with votes (currently an int) — also what
  makes quorum possible.
- **Live capture progress:** edit one status message through the stages
  (fetched → transcribed → cataloged) instead of a silent 20–30s wait.

### P1.3 · Editable cards
An ✏️ Edit button on a spot card → modal pre-filled with venue/vibe →
`POST /api/events/{id}/edit` updates metadata (+ best-effort re-embed); card
re-renders in place. *(This is also a fast manual path to fix a bad extraction.)*

### P1.4 · Onboarding that sells itself  *(roadmap 1.1)*
- `/setup` wizard: pick/create a drop channel, set the group's **home city**
  (powers distance / "near you"), done in 30s.
- First-capture magic-moment line on the bot's first card in a server.
- Capture-failure path: empathetic error + **manual-add modal** (venue, vibe,
  link) so the user never hits a dead end.

### P1.5 · Ambient value  *(roadmap 1.4)*
- **Weekly digest** to the drop channel ("your crew saved 7 spots this week · top
  pick: 🍜 Menya Hanabi (5 👍) · 3 still unplanned"). `markdown_digest.py` exists —
  wire it to a scheduler. Ship `/digest` on-demand first.
- `/browse [category]` pagination (10/page, ◀▶) — **built and deployed**
  (`app/bot.py` + `app/cards.py`). Follow-up: category filter polish, "all" view.
- `/catalog` v2: filter by category/area/scheduled.

### P1.6 · Card design pass + admin/share UI polish
Re-imagine the embed layout now that thumbnail/map/who's-in exist (mockups first
via `scripts/gen_ui_mockups.py`). Match the admin + share pages to the
dashboard's cleaner look (one accent, tabular numbers, consistent spacing).

### P1.7 · `bot.py` offline test coverage
`on_message` routing tests: muted channel, DM, multi-link. `bot.py` currently has
almost no direct coverage.

---

## P2 — Reach: more capture sources + the web surface

### P2.1 · More capture sources  *(roadmap 2.1, in order)*
1. **TikTok extractor** — the other half of food-reel culture; same og:-tag +
   embedded-JSON patterns as the IG one (`extractors/instagram.py` is the
   template, `extractors/base.select_extractor` routes by domain). Fixture-based.
2. YouTube Shorts + plain URLs (Eater lists, Google Maps share links —
   `extractors/generic.py` already does JSON-LD).
3. Screenshots — OCR for "my friend texted me this" images.
4. Video-frame OCR (sample frames, not just the cover) as an async enrichment
   pass, never in the capture path.

### P2.2 · Transcription experiment
One-time `WHISPER_MODEL=tiny` on the ~4 no-text-signal corpus rows → `eval.py`;
ship async-enrichment transcription only if it recovers ≥3 venues.

### P2.3 · Taste-aware suggestions  *(roadmap 2.2)*
Evolve the "Add suggestions" button from plain similarity search to a per-guild
**taste profile**: weight by votes and *attended* events (P1.1 data), decay stale
spots, diversify categories. "More like the places we actually went" is the moat.

### P2.4 · Web companion — evolve `admin.py`, don't rebuild  *(roadmap 2.3)*
- **Shareable read-only catalog page per guild** (`spotbot.app/g/<invite-code>`):
  the group's map, filterable list, vote counts — viewable by people who don't
  have the bot yet, with an "Add SpotBot to your Discord" button. **This is the
  growth surface.**
- Map view + category filters on the existing admin page for members.

---

## P3 — Growth (free-while-growing; don't gate anything yet)

- **Viral loop:** every shared catalog page + digest footer says "Powered by
  SpotBot — add it to your Discord." Optimize server→server spread.
- **Distribution:** Discord App Directory listing (the big one), top.gg /
  discord.bots.gg, Product Hunt, r/Discord + food-city subreddits.
- **Demo GIF** (paste reel → card in 10s) at the top of the README + landing
  page. A public demo server anyone can join.
- **Niche-first:** food-obsessed friend groups + college-city servers (Long Beach
  roots are the seed).
- **Starter packs:** on `/setup`, offer to seed 10 curated spots for the group's
  city so `/plan` works on day one.
- Future paywall lines (keep cheap to add, don't gate): captures/month per
  server, catalog size, taste-profile recs, multi-city, priority queue.

### Deferred until real users (Nick's call)
- `/plan` rework · the "we went!" loop (P1.1) beyond the minimum
- Dashboard capture feed persistence (currently in-memory, resets with the app)

---

## Why labeling matters

**The problem it solves.** Capture "works" — the pipeline runs end to end — but
the *fields it fills are frequently wrong*. On 2026-09-08 the live catalog had
**12 of 18 spots with `venue_name = "Locations"`** (a string scraped off
Instagram's page chrome, not a place). Others saved the container ("Disneyland")
instead of the tenant ("Bengal Barbecue"), or a decoy city, or a promo post that
should have been rejected. You cannot fix what you cannot measure, and "it looks
better now" is not a measurement.

**One file, three jobs.** `fixtures/labels.jsonl` is a hand-reviewed corpus —
one row per capture, holding the input the extractor saw, what it `predicted`,
and the `gold` truth. That single artifact is:

1. **A regression suite.** `test_extraction_labels.py` sets accuracy floors on
   the corpus; CI fails any change that regresses them. Every parser tweak is
   safe to make because a drop is caught automatically.
2. **An honest scorecard.** `eval.py` scores the extractor on a **frozen
   train/test split** (deterministic by URL hash, ~70/30). We tune on `train`
   and report `test`, so the numbers (venue ~50%, category ~63%, city ~73%) are
   real held-out accuracy, not corpus-fitting. This is how we know category
   keyword-matching has plateaued and where the next 15 points have to come
   from.
3. **A few-shot / correction bank.** Corrections feed straight back into
   accuracy: the `venue_aliases.json` table turns a repeated wrong→right pair
   into a deterministic override; the nearest labeled rows can be injected as
   worked examples into any future LLM prompt; repeated free-text `note`s
   ("it's the @handle after 'from'") get mined into new parser rules.

**It's the substrate for every lever we have.** The embedding-nearest category
classifier (P0.2) builds its per-category centroids *from labeled rows*. The
Haiku go/no-go experiment (P0.4) is literally "run Haiku over the corpus,
diff against gold" — impossible without labels. Eventual fine-tuning of a small
local model is `input → gold` pairs. Growing the corpus also widens the city
gazetteer and firms up the held-out numbers (a bigger test set = less noise).

**It's how we stay on the Zero-Dollar Stack.** The obvious way to get venue
names right is to buy Google/Foursquare Places lookups (~$400–600/yr) or cloud
vision. The label loop is the in-house alternative: a deterministic slot-first
parser (`@handle`, `from X`, `at X` with container-demote, `📍`/`in City`
gazetteer) measured against the corpus, with the local LLM only as a gap-filler.
That keeps marginal cost at $0 and keeps post text on our infrastructure — the
two constraints the "100 Nights Out" challenge is run under.

**We've proven the bottleneck is here.** Fed gold venue/city, the geocode stage
resolves city-only queries 100% of the time — geo is *not* the problem, the
extractor is. So labeling the extractor's output, and closing the loop so real
captures grow the corpus (P0.3), is where accuracy is actually won.

---

## Where the detail lives

| Doc | What |
|---|---|
| `docs/PRODUCT_ROADMAP.md` | phases, the "why", the recommended first slice |
| `docs/MILESTONES.md` | the July-2026 scorecard + the "100 Nights Out" challenge |
| `docs/EXTRACTION_ACCURACY.md` | the label loop — approach, round history, spam defense *(branch `extraction-label-loop`)* |
| `docs/HANDOFF.md` | current extraction state + round-6 targets *(branch `extraction-label-loop`)* |
| `docs/NEXT_SESSION.md` | the running build queue + carry-over notes |
| `docs/IG_AUTH_INGESTION_PLAN.md` | the authed-capture plan (P0.1) |
| `docs/RUNBOOK.md` | how to run the services locally |
