# Track A — Capture & Data Sources

**Specialist:** [Member 2 full name] · **Directed by:** Nick (Architecture & Platform)
**Points:** 460 across 6 features · **Demo-day claim:** *"I raised capture success on real
posts and recovered reels whose venue lives only in the comments."*

You own the front door. Everything downstream — accuracy, cards, votes, events — is
worthless if the reel never gets fetched. Your job is to make capture work on posts nobody
hand-picked, and to make it fail safely when it can't.

---

## 1. Handoff — what already exists

**The path a pasted link takes:** `app/cards.py` sends the URL to the ingestion service →
`src/ingestion/pipeline/orchestrator.py` runs the stages → fetch → transcribe → extract →
geo → temporal → save. Your area is the fetch stage and everything that feeds it.

| File | What it is |
|---|---|
| `src/ingestion/sources/ig_authed.py` | The logged-in Instagram fetch path (uses `IG_USERNAME` / `IG_PASSWORD` from `.env`) |
| `src/ingestion/pipeline/orchestrator.py` | `IngestionPipeline`, `RunReport`, the stage callbacks |
| `scripts/seed_corpus.py` | Batch-runs a list of URLs through capture — the seed of your harness |
| `scripts/pull_dm_reels.py`, `scripts/watch_dm_reels.py` | Where reel URLs come from |
| `test_ig_authed.py`, `test_ig_embed.py`, `test_tiktok.py`, `test_ig_live.py` | Existing coverage (`test_ig_live.py` is a live test — deselected by `-k "not live"`) |

**Honest numbers you inherit:** the only recorded live capture result is **13/13 on a
curated list** (July 2026) — a list somebody picked, on reels that were public at the
time. There is **no measured pass rate on random reels**, and the login wall has moved
since. Treat 13/13 as "it can work," not as a rate. Producing the real number is your
Phase 1 feature, and it is the number the whole semester's reliability story rests on.

**Traps that have already bitten people:**
- Instagram's `og:video` tag is often missing/wrong — the working mp4 comes from the
  `video_versions` JSON. Don't "fix" that back.
- The video download is the slow part. Timeouts here look like extraction failures.
- Reels where the venue is only in the comments are **not** capture failures — they are
  feature #15. Sort them as their own failure class from day one.
- Never commit `.env`, a session cookie, or anything under `data/`.

---

## 2. Your features

| # | Feature | Need | Pts | Phase |
|---|---|---|---|---|
| 1 | Live capture reliability harness | Must | 60 | 1 |
| 3 | Capture input hardening — SSRF + download safety 🔒 | Must | 60 | 1 |
| 2 | Authenticated capture + fallback chain | Must | 100 | 2 |
| 15 | Gatekept-reel recovery ★ | Should | 100 | 3 |
| 14 | Safe-link guard | Should | 40 | 4 |
| 30 | TikTok + YouTube Shorts capture | Nice | 100 | 4 |

★ unique to SpotBot · 🔒 your security feature

---

## 3. To-do list

### Phase 1 — Sep 15 – Oct 3 (120 pts)

**#1 Live capture reliability harness (60)**
- [ ] Collect 50+ reel URLs nobody hand-picked (`scripts/pull_dm_reels.py`, or a random
      slice of `fixtures/labels.jsonl` URLs).
- [ ] Write `scripts/capture_harness.py`: runs the batch unattended, one row per URL.
- [ ] Record for every URL: outcome, elapsed seconds, and a failure class —
      `login_wall`, `no_video`, `timeout`, `no_venue`, `not_a_place`, `other`.
- [ ] Emit a summary table + a JSON/CSV artifact that Nick's dashboard can read later.
- [ ] **Done when:** you can state the pass rate on 50+ random reels with the failure
      breakdown, and re-run it with one command.

**#3 Capture input hardening — SSRF + download safety (60) 🔒**
- [ ] Resolve the host before fetching; refuse private/loopback/link-local ranges
      (127/8, 10/8, 172.16/12, 192.168/16, 169.254/16, ::1, fc00::/7) **and** any
      redirect that lands on one.
- [ ] Cap download size (hard byte limit, enforced while streaming, not after) and check
      the content type before writing a file.
- [ ] Tests: an internal URL, a redirect to an internal URL, an oversized body, and a
      wrong content type are each refused — with no file written.
- [ ] **Done when:** those four tests pass in CI and a pasted `http://169.254.169.254/...`
      is refused before any network call. This is threat-model **T3**.

### Phase 2 — Oct 6 – Oct 24 (100 pts)

**#2 Authenticated capture + fallback chain (100)**
- [ ] Define one fetch interface so any source can be swapped (incl. a paid lookup later).
- [ ] Order the chain: logged-in → public page → embed page → "add it manually."
- [ ] Retry only temporary errors (timeout, 5xx, rate limit) with backoff; never retry a
      login wall or a deleted post.
- [ ] Re-run the Phase 1 harness and report the pass rate **before vs after**.
- [ ] **Done when:** the harness number goes up and you can show which fallback rung
      rescued each recovered reel.

### Phase 3 — Oct 27 – Nov 14 (100 pts)

**#15 Gatekept-reel recovery ★ (100)**
- [ ] Pull the top ~10 comments for a reel whose caption has no venue.
- [ ] Feed comment text into the extraction input as a *separate, clearly-labeled*
      field — Track B's grounding guard (#7) needs to know it came from comments.
- [ ] Split the outcome: "a real place with no name yet" (ask the group) vs "not a place"
      (reject). Never silently drop a real place.
- [ ] **Done when:** at least one comments-only reel from the corpus captures with the
      right venue, and the 16 `needs_review` rows in `fixtures/labels.jsonl` are re-run
      to see how many are now recoverable.

### Phase 4 — Nov 17 – Dec 11 (140 pts)

**#14 Safe-link guard (40)**
- [ ] Allow only Instagram, TikTok and YouTube link shapes; reject everything else
      before any network call, with a plain-language message.
- [ ] **Done when:** a pasted non-supported link is refused instantly, with a test.

**#30 TikTok + YouTube Shorts capture (100)**
- [ ] Move the TikTok extractor from test pages to live pages (`test_tiktok.py` is your
      starting point).
- [ ] Add YouTube Shorts as a third source behind the same fetch interface from #2.
- [ ] **Done when:** one TikTok and one Short each produce a spot card end to end.

---

## 4. Commands you live in

```bash
pytest -k "not live" -q
```
```bash
python scripts/seed_corpus.py reels.txt --skip-existing --out fixtures/labels.batchN.jsonl
```
```bash
python scripts/smoke_ingest.py
```

---

## 5. Handoffs you owe, and receive

**You owe Track B (Accuracy)** — by **end of Phase 1**: the failure taxonomy from #1.
B's numbers are only meaningful once "the extractor was wrong" is separated from "the
video never downloaded." Give them the failure-class field name and the artifact path.

**You owe Track B again** — during **Phase 3**: tell them the moment comment text starts
entering the extraction input (#15), and with what field name. Comment text is attacker-
controlled; their grounding guard (#7) must treat it as hostile and must know it exists.

**You owe Track D (Cybersecurity)** — by **mid-Phase 1**: a list of every outbound fetch
your code can make. D's simulated attack (#12) targets exactly those.

**You owe Track C (Experience)** — by **Phase 2**: the final failure-class names, so
their "clear failure messages" (#19) say the right thing per class.

**You receive from Nick** — Phase 2: capture moves to a background job (#24). Your fetch
path must report stage progress through the `on_stage` callback in `orchestrator.py`
rather than assuming it runs inline.

**You receive from Track D** — Phase 1: the attack run will probe your fetch path. Fix
what it finds; don't argue with it.

---

## 6. Where to ask

Post in the team channel with: the URL class you're on, the command you ran, and the
output. Nick reviews every PR — tag him. If a capture question is really an accuracy
question (the fetch worked, the venue is wrong), it belongs to Track B.
