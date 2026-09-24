# Track C — Experience & User Research

**Specialist:** [Member 4 full name] · **Directed by:** Nick (Architecture & Platform)
**Points:** 470 across 9 features · **Demo-day claim:** *"I ran a study with real users;
I cut time-to-first-card and raised the SUS score."*

You own whether a group that isn't us can actually use this. That is two jobs: building
the Discord surface (onboarding, cards, messages, browsing), and **measuring real people
using it** — the study is what makes your track evidence instead of opinion.

---

## 1. Handoff — what already exists

| File | What it is |
|---|---|
| `app/bot.py` | The Discord client, slash commands, persistent buttons (`DynamicItem`) |
| `app/cards.py` | Spot cards, vote buttons, capture transport, `stage_line()` progress text |
| `src/ingestion/serving/discord_format.py` | How a record becomes card text |
| `src/ingestion/serving/admin.py` | The admin/web endpoints, including the share page |
| `app/test_bot.py`, `app/test_cards.py`, `test_discord_format.py`, `test_went.py` | Existing coverage |
| `docs/USER_GUIDE.md` | What the bot does today, from a user's point of view |

**What works today:** paste a reel → card with vote buttons → quorum → Discord Scheduled
Event → next-day "did you go?" follow-up. `/plan` reads recent chat and proposes an
outing. `/browse` exists with pagination. A read-only share link exists.

**What doesn't:** there is no onboarding at all — a new server has to be configured by
hand, which is why `/setup` is your Phase 1 Must. Failure messages are generic. Nothing
measures how long a capture takes from the user's side.

**Traps:**
- The bot sets `allowed_mentions=discord.AllowedMentions.none()` at the client level
  (fixes threat-model **T1**, mention injection). **Do not** pass a different
  `allowed_mentions` on a message or a follow-up to "make a ping work."
- Persistent buttons must survive a bot restart — that's why they're `DynamicItem`s with
  IDs encoding their target, not closures.
- Every call the bot makes to the services carries an `X-Tenant-Token` header
  (`app/tenant_auth.py`). If you add a new call, it needs the header or it gets a 403.
- Share links mint a **read-scoped** token. Never widen that to `rw`.
- Don't put a user's Discord ID, message text, or any token into a URL query string.

---

## 2. Your features

| # | Feature | Need | Pts | Phase |
|---|---|---|---|---|
| 8 | /setup onboarding wizard | Must | 100 | 1 |
| 18 | Time-to-card measurement | Should | 30 | 2 |
| 20 | In-Discord feedback + survey ★ | Should | 40 | 2 |
| 36 | Distance + map on spot cards | Nice | 40 | 2 |
| 21 | Privacy & data-deletion UX — /privacy + delete my data ★ 🔒 | Should | 60 | 3 |
| 22 | Usability fixes from the study | Should | 60 | 3 |
| 19 | Clear failure messages | Should | 30 | 4 |
| 35 | Catalog browsing v2 | Nice | 60 | 4 |
| 37 | Accessibility pass | Nice | 50 | 4 |

★ unique to SpotBot · 🔒 your security feature

---

## 3. To-do list

### Phase 1 — Sep 15 – Oct 3 (100 pts)

**#8 /setup onboarding wizard (100)**
- [ ] Step-by-step: pick the channel to watch for reels, set the group's home city,
      confirm, done.
- [ ] Store per-server settings through the existing per-guild path — never a global.
- [ ] Handle re-running `/setup` on an already-configured server without wiping data.
- [ ] Time it on a genuinely fresh server, with a stopwatch.
- [ ] **Done when:** a brand-new server is posting its first card in **under a minute**,
      and you have the recording or the timing to prove it.

### Phase 2 — Oct 6 – Oct 24 (110 pts)

**#18 Time-to-card measurement (30)**
- [ ] Log elapsed time from paste → card posted, per capture.
- [ ] Expose median and p95 to Nick's operations dashboard (#29).
- [ ] **Done when:** you can quote today's median and p95, and watch them move.

**#20 In-Discord feedback + survey ★ (40)**
- [ ] `/feedback` for bug reports and ideas (stored per server, no message scraping).
- [ ] The 10-question SUS survey, taken inside Discord, for study participants.
- [ ] **Done when:** you can collect a SUS score from a participant without leaving Discord.

**#36 Distance + map on spot cards (40)**
- [ ] Show distance from the group's home city and a map link on each card.
- [ ] **Done when:** cards show both, and a spot with no coordinates degrades cleanly.

### Phase 3 — Oct 27 – Nov 14 (120 pts)

**#21 Privacy & data-deletion UX ★ 🔒 (60)**
- [ ] `/privacy` explains in plain language what's stored, where, and for how long.
- [ ] A one-command "delete this server's data" that actually purges the per-guild Chroma
      collection **and** the raw files — not just hides them.
- [ ] Require a confirmation step, and make it obvious the deletion is irreversible.
- [ ] Test: create a test server's data, delete it, assert nothing comes back from any
      endpoint or file path.
- [ ] **Done when:** that end-to-end test passes. This is threat-model **T6**.

**#22 Usability fixes from the study (60)**
- [ ] Run usability round 1 with **6–8 participants** who are not on the team. Script it:
      same tasks, same order, notes per participant, SUS at the end.
- [ ] Rank the findings; fix the top three.
- [ ] **Done when:** the three fixes are merged and you can show the before/after SUS.

### Phase 4 — Nov 17 – Dec 11 (140 pts)

**#19 Clear failure messages (30)**
- [ ] One plain-language message per failure class from Track A's taxonomy (private reel,
      no video, timeout, no venue, not a place) — each saying what to do next.
- [ ] **Done when:** no user-visible failure is a bare error or a silent nothing.

**#35 Catalog browsing v2 (60)**
- [ ] `/browse` filters by category, area, and whether a spot has a date set.
- [ ] **Done when:** the filters work on a server with 50+ spots without a wall of text.

**#37 Accessibility pass (50)**
- [ ] Alt text on card images, readable contrast, screen-reader labels on cards and the
      share/web pages.
- [ ] **Done when:** the checklist is completed and recorded in the final report.

---

## 4. Commands you live in

```bash
pytest -k "not live" -q
```
```bash
python -m app.bot
```

---

## 5. Handoffs you owe, and receive

**You owe Nick** — **end of Phase 1**: the `/setup` timing, plus per-server settings keys
you added (they have to survive deployment to staging in Phase 3).

**You owe Nick** — **Phase 2**: the time-to-card metric names/shape, so the dashboard
(#29) can chart them rather than inventing its own.

**You owe the whole team** — **Phase 3**: the usability round-1 findings, ranked. Fixes
outside your area (a capture wait, a wrong venue) get handed to A or B with the
participant quote attached, not silently absorbed.

**You owe Track D (Cybersecurity)** — **Phase 3**: the deletion path. D will test that
deleted data is actually gone and that one server can't trigger another's deletion.

**You receive from Track A** — **Phase 2**: the final failure-class names for #19.
**From Track B** — **Phase 3**: the confidence behind the place-or-not gate, so a card can
say "a real place, name unclear — what is it?" instead of failing.
**From Nick** — **Phase 2**: non-blocking capture (#27) with a live stage line; your
messages must update in place rather than posting a new message per stage.

---

## 6. Where to ask

Bring screenshots. A UX claim with a screenshot and a participant quote wins every
argument in this project; a UX claim without one loses. Nick reviews every PR.
