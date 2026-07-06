# Milestones — July 2026 and The Challenge

Written 2026-07-06. Current state: the full loop (reel → card → votes → quorum →
calendar event) works locally; 178 offline tests; capture measured 100% on the
live set; multi-tenant catalogs; share page; fail-fast hardening. What's missing
is location smarts, real-world hours, and other people.

---

## Where we are by July 31 — "v1.0 dogfood"

Four weeks, one theme per week. Each week ends with something demonstrable.

### Week 1 (Jul 6–12) — The bot learns *where*
Execute `docs/NEXT_SESSION.md`:
- "I'm near X" → distance-ranked suggestions · midpoint-between-users mode
- Transcribe + OCR validated on real reels (results tables filled in)
- Ease-of-use v1 live smoke · codebase cleanup (UTF-16 reqs, legacy containers, full-suite CI)
- **Demo at week's end:** two people say where they are, `/plan` answers with a fair-for-both spot and distances.

### Week 2 (Jul 13–19) — Capture more, close the loop
- **TikTok extractor** (other half of reel culture) + `/share` command + weekly digest post
- **The went-there loop**: day-after "did you go? 🌟" prompt on locked-in events —
  this is the counter The Challenge runs on, so it ships this week
- Bot-handler offline tests (routing, mute, multi-link) — `bot.py` gets real coverage
- **Demo:** a TikTok becomes a card; a past event asks how it went.

### Week 3 (Jul 20–26) — Other humans
- Deploy the stack on an always-on box (spare PC or cheapest VPS; `llama3.2:3b` profile)
- Onboard **3 real friend-group servers** (not test servers — people who actually go out)
- Usage metrics logging: captures, success rate, time-to-card, spots→plans→attended
- **Demo:** a capture from someone who isn't us, on infrastructure we didn't touch that day.

### Week 4 (Jul 27–31) — Harden and tag
- Fix whatever real usage breaks (expect: IG edge cases, concurrent captures → start
  the job queue if it hurts, Discord permission weirdness)
- Demo GIF for the README (paste → card in real time), LICENSE decision, **tag `v1.0`**

### The July 31 scorecard (pass/fail, no vibes)
| Metric | Target |
|---|---|
| Real servers active weekly | **3** |
| Wild capture success rate (logged, not test-set) | **≥ 90%** |
| Time-to-card p50 (speed profile) | **< 25s** |
| Reel → attended event, fully traced | **≥ 1** |
| Offline tests | **≥ 200**, full suite in CI |
| Repo | cleaned, tagged v1.0 |

---

## ⚔️ The Challenge — not done in a month, maybe not in a year

> ## 100 Nights Out
> **One hundred confirmed real-world outings — reel pasted → card → votes →
> calendar event → "yes, we actually went" — across all servers, by July 2027.**

Why this is the right mountain:
- It's the **north-star metric made physical**. Not installs, not captures, not
  stars on GitHub — *nights that happened because the software existed*. Every
  fake or vanity win is excluded by construction: the counter only moves when
  the went-there loop confirms attendance.
- It's **honest about difficulty**. 100 nights needs roughly 15–20 genuinely
  active groups averaging an outing a month for most of a year. That means
  strangers adopting it, capture surviving IG's changes for 12 months, and
  recommendations good enough that groups keep coming back. No single sprint
  can fake it.
- **Every feature gets a judge.** "Does this help a group actually go out?"
  kills scope creep better than any roadmap review.

### The engineering half of the challenge (Claude's side)
Get there **on the Zero-Dollar Stack**: marginal cost per server stays at $0 —
local/pooled Ollama, OpenStreetMap, free-tier hosting, no paid resolver unless
the free path drops below 90% measured. Privacy story intact the whole way
(post text never leaves infrastructure we control). Scaling a real user base
without buying the usual shortcuts is the hard engineering road, and it's the
one that keeps the product's soul.

### Checkpoints (so we know we're losing early)
| Date | Nights counter | Also true |
|---|---|---|
| Aug 1, 2026 | counter live, **≥ 1** | went-there loop shipped, 3 dogfood servers |
| Oct 1, 2026 | **≥ 10** | first server run by someone we've never met |
| Jan 1, 2027 | **≥ 35** | 1,000 spots cataloged · IG + TikTok + Shorts capture |
| Apr 1, 2027 | **≥ 65** | a `/plan` pick is accepted ≥ 30% of the time |
| **Jul 6, 2027** | **💯** | still $0 marginal cost, still private |

Miss two checkpoints in a row → we stop and rethink the product, not the effort.

*Counter definition: one night = one Discord Scheduled Event created through
SpotBot whose went-there prompt got at least two "we went" confirmations from
different users. Duplicates and bot-authored events don't count.*
