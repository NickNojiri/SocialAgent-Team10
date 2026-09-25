# SpotBot — Team Priority TODO

Last updated 2026-09-24. Owner: Nick. One prioritized list for the whole team.

This is the "what do I pick up next" list. It rolls up `docs/PRODUCT_ROADMAP.md`
(the why), `docs/MILESTONES.md` (the 100 Nights challenge), `docs/NEXT_SESSION.md`
(the build queue) and `docs/EXTRACTION_ACCURACY.md` / `docs/HANDOFF.md` (the label
loop) into a single ranked backlog. Who owns what, and when: `docs/CAPSTONE_PLAN.md`.

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

## Status on 2026-09-24: Phase 1 closes Oct 3

The per-track plan lives in `docs/tracks/`; this list is the product view of the same
work. Items below marked **✓ built** have shipped on `main` since this list was last
updated.

| Track | Phase 1 feature | State |
|---|---|---|
| A · Capture | #1 live capture harness, #3 SSRF + download limits | Not started. Tracks B and D and the /dash over-time view wait on the harness artifact and the failure taxonomy |
| B · Accuracy | #4 scorecard with intervals + McNemar, #5 labeler field + kappa | Not started (a Wilson interval exists in `scripts/eval_diagnostics.py` only) |
| C · Experience | #8 `/setup` | Built. Left: the stopwatch run on a fresh server |
| D · Security | #12 attack suite | Not started. Seed handed over: `docs/handoffs/TRACK-D-AUTHZ-BENCH.md` |
| Platform | #9, #23, #24, #25 | Done. Bot changes written up for Track C: `docs/handoffs/TRACK-C-BOT-CHANGES.md` |

Built ahead of plan (Phases 2–4): #10 key rotation, #11 staging stack (needs a host),
#18 time-to-card, #19 failure messages (app half; final taxonomy is Track A's), #20
feedback + SUS survey, #21 `/privacy` + delete, #26 retries, #27 async by default, #28
rate limits (built, all limits 0 = off until Nick sets the numbers), #29 /dash panels +
load test, #35 `/browse` filters, #36 map + distance, #37 accessibility pass (two
screen-reader checks left).

Evidence for the phase, as each track reports it: `docs/PHASE1_EVIDENCE.md`.

---

## P0 — Foundation: capture works, and we can prove what it extracts

### P0.1 · Capture reliability → 95–99%  *(roadmap 0.1)*
The product-killer if it slips. The free (unauthenticated) path was designed
around a ~40–70% estimate, then hit 13/13 on a curated set (2026-07-05) — but
13 hand-picked links is not a pass rate. A product that fails a third of
first-time pastes retains nobody, so measure it on reels nobody picked.
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

### P0.2 · Extraction accuracy — the label loop  *(`docs/EXTRACTION_ACCURACY.md`)*
Capture runs end to end but the *fields it fills are often wrong*. This is the
current active workstream. See the "Why labeling matters" section below.

Honest held-out numbers today (`python -m src.ingestion.eval --offline --split test`,
scored with the train-only alias table):

| metric | held-out |
|---|---|
| venue exact | **37.4%** |
| category | 63.2% |
| city | 73.1% |
| promo-rejection (is_vague) | 23.5% recall · 50.0% precision |

> Venue was reported as ~50% until 2026-09-10, when test rows' own gold labels were
> found compiled into the alias table that scored them. Write-up and reproduction:
> `docs/ML_REVIEW_QUESTIONS.md`, `python scripts/eval_diagnostics.py`.

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
The clean test of "does a cheap LLM beat the honest ~37% venue / ~63% category?"
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
   *Partly built (#25): a reel already queued or running joins that job. A reel
   captured earlier is still fetched again; the content-hash upsert stops only the
   duplicate row.*
2. **Confidence gate on the LLM** — only captures below a confidence threshold
   (which slot filled the venue) ever reach the model.
3. **Global daily budget cap** — hard counter of N LLM calls/day; on hit →
   heuristic-only + log. *✓ built as a daily capture cap (#28), off until set.*
4. **Per-user + per-guild rate limits** — token bucket (e.g. 10/user/10min,
   60/guild/hour); over → friendly "slow down" reply. *✓ built (#28): sliding
   windows, a "try again in N minutes" reply; all limits 0 = off until Nick sets them.*
5. **Lock the admin API** — the always-on box's systemd unit (not in git) binds
   `0.0.0.0:8010`; local runs (`run_local.ps1`, `make`) already get uvicorn's
   `127.0.0.1` default. On the box: bind `127.0.0.1` + reach it from the container
   over the docker bridge, or add a shared-secret header. *Staging (#11) publishes
   only `127.0.0.1` ports; the always-on box's own unit is unchanged.*
6. **Discord-side raid guard** — ignore messages with > K links; optionally
   ignore links from accounts that joined < N minutes ago.
7. **SSRF guard on `/api/ingest`** — host allowlist (instagram.com, tiktok.com)
   plus private-IP rejection before the video download. `THREAT_MODEL.md` T3.
   *Open: Track A's #3, Phase 1.*
8. ~~Mention injection~~ — done 2026-09-15: the client sends with
   `AllowedMentions.none()`, so a venue called "@everyone" can never ping
   (`THREAT_MODEL.md` T1).
9. ~~Tenant-token authorization~~ — done 2026-09-17: every catalog call is
   signed per guild (and per user for votes), on both :8010 and :8003
   (`THREAT_MODEL.md` T2). Left: set `SPOTBOT_SIGNING_KEY` on the always-on box (T8).
   *T8 closed on staging (#11); key rotation built (#10).*

The full analysis, with what is fixed / built / open: `docs/THREAT_MODEL.md`.

### P0.6 · Multi-tenancy — required for a hosted bot  *(roadmap 0.2)*
*✓ built differently (#9): one Chroma collection per guild (ADR-0001) and a signed
per-guild token on every call, rather than a `where` filter and `/api/guilds/{gid}`
paths.*
- Scope everything by `guild_id`: one Chroma collection with a `guild_id`
  metadata filter (`where`), channel config moved from `channels.json` into the
  `db` service.
- `IngestionPipeline` / `RecommendationService` take a `guild_id`; `admin.py`
  endpoints become `/api/guilds/{gid}/...`.

### P0.7 · Hosted architecture basics  *(roadmap 0.3)*
- **Job queue for ingestion.** Built as a spike (`src/ingestion/serving/jobs.py`,
  ADR-0004, behind `INGEST_ASYNC=1`): enqueue → worker → bot edits its "⏳" message
  with the stage. *✓ On by default since #27 (ADR-0005).*
- **Durable, idempotent capture jobs** — *✓ all four built (#23–#26); ADR-0005's
  Evidence section still waits on real capture numbers.* 491A features **#23–#26** (New, Sept 23), Nick,
  started now; #23–#25 in Phase 1, #26 in Phase 3. Nick owns them, and each is scoped
  small enough that another specialist can take one over if he doesn't finish it.
  The queue is in-process, so a restart loses in-flight work and the same reel pasted
  twice is captured twice. Five PRs, in order:
  1. Per-job stage timings via the existing `on_stage` callback, plus
     `scripts/summarize_captures.py` (duration distribution, captures past 300 s and
     180 s, duplicate counts).
  2. SQLite-backed `JobStore` behind the existing interface, chosen by config
     (in-memory stays the default); on startup, jobs left `running` are requeued once,
     then failed with a reason.
  3. Idempotent submission keyed on hash(guild_id, normalized URL) — a duplicate while
     queued or running returns the existing `job_id`.
  4. Retry transient failures only (fetch timeout, network) with capped exponential
     backoff; extraction failures never retry. `attempts` + `last_error` stored, failed
     jobs listed in the admin API.
  5. `docs/adr/0005-sqlite-job-store.md`, superseding ADR-0004's in-memory choice.
  6. Bot side, separate PR (`app/`): the Retry button polls the existing `job_id` instead
     of resubmitting, and the status line says "still working" after 180 s rather than
     showing a failure.

  Constraints: no new services or dependencies (no Redis/Kafka/Postgres), `INGEST_ASYNC`
  default unchanged, tenant-token checks preserved on every guild call (fail closed,
  503), all existing tests pass. Platform and `app/` changes ship as separate PRs.
- Pick the always-on box for the dogfood deploy (spare PC or cheapest VPS,
  `llama3.2:3b` profile). *Staging stack built (#11, `docs/RUNBOOK.md`); still needs a host.*
- ToS + privacy-policy pages *(privacy: ✓ `/privacy` in Discord, #21; ToS still open)* (capture is user-initiated paste only — never
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
*✓ thumbnail, map link + distance (#36), who's in, and live capture progress (#27) are
all built.*
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
*✓ built: `EditSpotModal` in `app/cards.py`.*
An ✏️ Edit button on a spot card → modal pre-filled with venue/vibe →
`POST /api/events/{id}/edit` updates metadata (+ best-effort re-embed); card
re-renders in place. *(This is also a fast manual path to fix a bad extraction.)*

### P1.4 · Onboarding that sells itself  *(roadmap 1.1)*
- `/setup` wizard: pick/create a drop channel, set the group's **home city**
  (powers distance / "near you"), done in 30s. *✓ built (#8); timing on a fresh
  server still to do.*
- First-capture magic-moment line on the bot's first card in a server. *✓ built
  (`_maybe_first_card_tip`).*
- Capture-failure path: empathetic error + **manual-add modal** (venue, vibe,
  link) so the user never hits a dead end. *✓ built: #19 messages + `SpotModal`.*

### P1.5 · Ambient value  *(roadmap 1.4)*
- **Weekly digest** to the drop channel ("your crew saved 7 spots this week · top
  pick: 🍜 Menya Hanabi (5 👍) · 3 still unplanned"). `markdown_digest.py` exists —
  wire it to a scheduler. Ship `/digest` on-demand first.
- `/browse [category]` pagination (10/page, ◀▶) — **built and deployed**
  (`app/bot.py` + `app/cards.py`). Follow-up: category filter polish, "all" view.
- `/catalog` v2: filter by category/area/scheduled. *✓ as `/browse` filters (#35).*

### P1.6 · Card design pass + admin/share UI polish
Re-imagine the embed layout now that thumbnail/map/who's-in exist (mockups first
via `scripts/gen_ui_mockups.py`). Match the admin + share pages to the
dashboard's cleaner look (one accent, tabular numbers, consistent spacing).

### P1.7 · `bot.py` offline test coverage
`app/test_bot.py` already covers `on_message` routing: link anywhere, TikTok,
muted channel, bot authors, DM welcome, silent plain chat. Still missing: a
message with **several links** (one capture call carrying every URL, duplicates
collapsed), and anything past routing — `handle_reel_capture`'s status/failure
replies and the slash commands.

---

## P2 — Reach: more capture sources + the web surface

### P2.1 · More capture sources  *(roadmap 2.1, in order)*
1. **TikTok extractor** *(✓ built: `extractors/tiktok.py`)* — the other half of food-reel culture; same og:-tag +
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
   and report `test`, so the numbers (venue ~37%, category ~63%, city ~73%) are
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
| `docs/EXTRACTION_ACCURACY.md` | the label loop — approach, round history, spam defense |
| `docs/HANDOFF.md` | current extraction state + round-6 targets |
| `docs/NEXT_SESSION.md` | the running build queue + carry-over notes |
| `docs/IG_AUTH_INGESTION_PLAN.md` | the authed-capture plan (P0.1) |
| `docs/RUNBOOK.md` | how to run the services locally |
