# SpotBot — CSULB Capstone Sprint Plan (Fall 2026 → Spring 2027)

> Last updated 2026-09-14. The ranked backlog (the *what*) is `docs/TEAM_TODO.md`; this doc is
> the *who* and *when*.

## Context

SpotBot is a working Discord bot: paste an Instagram reel → it pulls the caption (and an audio
transcript), extracts venue/city/category with a slot-first parser, geocodes it, and posts a
votable "spot card." `/plan` reads a group chat and proposes an outing; who's-in votes hit quorum
and create a Discord Scheduled Event. Already in place: 19 test files with CI running the full
offline suite, a public share page, a **417-reel hand-labeled corpus** with a frozen train/test
split, a product roadmap (`docs/PRODUCT_ROADMAP.md`), and a ranked backlog (`docs/TEAM_TODO.md`).

### Where it actually stands

Held-out scorecard — `python -m src.ingestion.eval --offline --split test`:

| metric | held-out | what it means |
|---|---|---|
| venue exact | **37.4%** | reported as 49.6% until test labels were found leaking into the alias table |
| category | **63.2%** | only 8.5 points above always guessing `food_drink` |
| city | **73.1%** | geocoding is not the bottleneck — extraction is |
| promo rejection | **23.5%** recall · **50%** precision | half of rejections throw away a real place |

Capture is also blocked on the authenticated path (burner account flagged, IP blacklisted), and
the unauthenticated live pass rate (~40–70%) has never been measured on a clean network. Full
detail: `docs/HANDOFF.md`, `docs/ML_REVIEW_QUESTIONS.md`.

**Two facts drive this entire plan:**

1. **Nick built ~all of it.** In a capstone, "one person did the work" is a grading problem *and*
   a burnout problem. Scope must be carved so three other people own real, separable subsystems.
2. **The teammates are beginners.** So every track needs a ramp ladder (read → tiny PR → real
   feature), a module boundary that can't break the critical path, and copy-paste agent prompts.

**The reframe that makes this a capstone instead of a hackathon project:** stop measuring success
in features and start measuring it in **evidence**. The project already has the best capstone
hook available: honest, measured quality gaps — and the discipline to report them. The extractor
gets the venue right 37% of the time on held-out data, and the team caught its own evaluation
inflating that number. Moving those numbers, and defending the movement with confidence intervals,
is worth more than five new features.

**Outcome we're aiming at:** a deployed, multi-tenant system with a published reliability and
accuracy benchmark, a real user study, and four people who can each stand up and defend a
subsystem they personally own.

> ⚠️ Confirm every date against your syllabus and academic calendar — the week ranges below are
> estimates. Confirm deliverable formats (SRS template, poster size, report length) with your
> advisor in Week 1; they override anything here.

---

## Team structure — four individual sections

Each person owns a **named subsystem**, a **measurable claim**, and a **section of the final
report**. Nobody is "helping." Ownership means: you write it, you test it, you demo it, you
answer questions about it.

| Person | Track | Owns | The claim they defend at demo day |
|---|---|---|---|
| **Nick** | **Architecture & Platform** | Pipeline orchestration, async job queue, multi-tenancy, deployment, release engineering, code review, the demo | "I designed the ingestion architecture, made capture non-blocking, and deployed it multi-tenant." |
| **Teammate 1** | **A — Capture & Data Sources** | `sources/ig_authed.py`, fallback/retry, failure taxonomy, gatekept-reel recovery, more platforms | "I raised capture success from X% to Y% on N real posts and recovered reels whose venue lives only in comments." |
| **Teammate 2** | **B — Accuracy & Evaluation** | The label corpus, eval rigor (intervals, splits), category/venue/promo accuracy, hallucination guard | "I made the benchmark trustworthy and moved held-out venue from 37% to Y% and category from 63% to Z%." |
| **Teammate 3** | **C — Experience & User Research** | Discord UX, onboarding, web companion + map, accessibility, usability study | "I ran a study with N users; time-to-first-card fell from X to Y and SUS rose to Z." |

> **With a fifth person:** split Platform out of Nick's track into **Track D — Platform, Security
> & Ops** (multi-tenancy, admin API lockdown, deployment, observability, threat model).

### Anti-bottleneck rules (non-negotiable)
1. **Nick reviews; Nick does not implement another track's code.** If a teammate is stuck >1 day,
   Nick pairs with them for 45 minutes — screen shared, *they* type.
2. **No self-merge.** Every PR needs one non-author approval. This forces cross-reading.
3. **Every person demos their own work** at the biweekly review. If Nick demos it, it isn't owned.
4. **Track boundaries are module boundaries** — see "Why these tracks don't collide" below.
5. **You must be able to explain any code you ship.** Agents are allowed (encouraged), but if you
   can't explain it in review, it doesn't merge. This is the rule that protects you at demo day.

### Why these tracks don't collide (low merge conflict by design)
- **A** lives in `src/ingestion/sources/` + `extractors/` — a defined interface (`fetch_shortcode`
  → `RawPostSnapshot`) already specified in `docs/IG_AUTH_INGESTION_PLAN.md`.
- **B** lives in `src/ingestion/eval.py`, `pipeline/normalizer.py`, `fixtures/labels.jsonl`,
  `scripts/review.py`, `serving/recommender.py`.
- **C** lives in `app/` (Discord) + `serving/admin.py` templates.
- **Nick** owns `pipeline/orchestrator.py`, `serving/tenant_auth.py`, `.github/workflows/`,
  deployment config, and anything touching two tracks.

---

## Week 0 — Kickoff. Everyone, together.

Goal: **every teammate runs the project on their own laptop, picks a track, and leaves with a
first task.** A beginner who has shipped once behaves completely differently from one who hasn't.

- [ ] Before the meeting, at home: install Python 3.11+, Git, VS Code, Ollama; `ollama pull mxbai-embed-large`
- [ ] Clone the repo (`main`) → `.\scripts\setup.ps1`
- [ ] First run, no Discord token needed: `pytest -k "not live" -q`, then
      `python -m src.ingestion.eval --offline --split test` — everyone sees the same 37.4%
- [ ] Nick demos the bot live (with a recorded fallback)
- [ ] Each person picks a track, then one small first task with a due date
- [ ] Agree on: meeting cadence, standup channel, definition of done, PR rules
- [ ] Discord bot tokens + a shared test server: Sprint 1, not day one

**Deliverable:** 3 working environments, 3 chosen tracks, 3 first tasks, a team charter in
`docs/TEAM.md` (roles, cadence, decision process, how disagreements resolve).

---

## Semester 1 — Fall 2026 (~13 weeks, 6 sprints). Theme: **"Prove it works."**

Grade target: trustworthy numbers, a real architecture, and a working demo.

### Sprint 1 (≈ Sept 15–26) — Trustworthy baseline
You cannot improve what you can't measure honestly.
- **A:** Run `test_ig_live.py` on all 19 known URLs, unauthenticated, on a clean network.
  **Publish the real pass rate.** Build a failure taxonomy (login wall / no video / timeout / no venue).
- **B:** Make the scorecard trustworthy: finish the 16 `needs_review` rows, apply the two pending
  label fixes in `HANDOFF.md`, and print a 95% confidence interval next to every metric — at
  n≈120 a 5-point change is inside the noise.
- **C:** Instrument and measure **time-to-card** today (p50/p95). Screen-record the current UX as
  the "before."
- **Nick:** Architecture diagram + first 3 ADRs (why Chroma, why heuristic-first extraction, why
  Discord-first). Lock down the admin API (it binds `0.0.0.0:8010`).
- **Deliverable:** `docs/BASELINE.md` — every current number in one table, with intervals.

### Sprint 2 (≈ Sept 29–Oct 10) — Requirements & the first real gain
- **A:** Unblock capture: fresh burner on a different network, or the paid-resolver fallback behind
  the same interface. Measure the authed pass rate against the Sprint 1 baseline.
- **B:** Category round 6 — embedding-nearest classifier (`mxbai-embed-large` centroids built from
  **train only**). Report the change with a paired test on the same rows, not a point estimate.
- **C:** `/setup` onboarding wizard + live capture progress message (replaces the silent wait).
- **Nick:** **SRS / requirements doc** (functional + non-functional, measured numbers as NFR
  targets). Everyone contributes their track's requirements. Finish `guild_id` scoping.
- **Deliverable:** SRS v1 submitted.

### Sprint 3 (≈ Oct 13–24) — Accuracy & the hallucination problem
- **A:** Retry + fallback chain (authed → unauthenticated → embed → manual). Failure taxonomy v2.
- **B:** **The "is this even a place?" problem.** Promo rejection has 50% precision — it throws
  away real places as often as it catches promos. Two real motivating failures are in the data: a
  *Modern Family* comedy clip force-fit into a venue, and a summary that invented a venue name that
  was never said. Add a grounding check that rejects venue names absent from caption, transcript,
  and tag. Measure precision and recall before/after.
- **C:** Manual-add modal for failed captures + error-state copy. Recruit user-study participants.
- **Nick:** Async job queue design (ADR) + spike. Prompt-injection threat model — captions are
  untrusted text (OWASP LLM01).
- **Deliverable:** `docs/EVAL_REPORT_v1.md` with real accuracy numbers and intervals.

### Sprint 4 (≈ Oct 27–Nov 7) — Make it usable by strangers
- **A:** **Recover gatekept reels** — the venue is only in the comments for some posts. Add a
  comment-scrape step to the capture ladder (caption → transcript → OCR → comments → give up), and
  separate "no-name place" (ask the user) from "not a place" (reject). Transcripts carry the venue
  in only ~6% of rows, so this beats more audio work.
- **B:** Geo-aware ranking — lat/lng are stored but unused in `recommender.py`. Rank by distance
  from the guild's home city + popularity.
- **C:** **Usability study round 1** — 6–8 participants, think-aloud, SUS questionnaire.
- **Nick:** Implement the job queue; capture becomes non-blocking. Deploy a staging instance.
- **Deliverable:** Live staging URL + usability findings.

### Sprint 5 (≈ Nov 10–21) — Integrate & harden
- **A:** TikTok extractor from fixtures to live pages; add YouTube Shorts.
- **B:** Eval round 2 on the improved pipeline; charts for the report.
- **C:** Act on study findings (top 3 fixes); accessibility pass (contrast, alt text, screen reader).
- **Nick:** Integration freeze; load test (how many concurrent captures before it degrades?);
  security review doc.
- **Deliverable:** Load test + security review docs.

### Sprint 6 (≈ Nov 24–Dec 12) — Midterm demo & write-up
- Feature freeze at the start of this sprint. **Bugs and docs only.**
- Record a 3-minute demo video (paste reel → card → `/plan` → scheduled event).
- **Semester 1 report** — each person writes their own section.
- Retrospective + Semester 2 backlog groomed.
- **Deliverable:** Demo + report + tagged release `v0.1`.

**Semester 1 exit criteria:** measured capture reliability, a published eval report with
confidence intervals, a live staging deployment, a completed user study, three people who each
shipped.

---

## Semester 2 — Spring 2027 (~17 weeks, 8 sprints). Theme: **"Make it real."**

Grade target: a deployed product with real users, a second round of evidence, and a polished defense.

### Sprint 7 (≈ Jan 19–30) — Production readiness
- **A:** Source reliability SLO; alerting when pass rate drops below target.
- **B:** The Haiku go/no-go: does a cheap hosted LLM on low-confidence rows beat the heuristic on
  a **human-only** held-out slice? (A Claude model helped write some labels — score it cleanly.)
- **C:** Web companion: map view with spots plotted (OSM tiles), category filters.
- **Nick:** Production deploy, backups, secrets rotation, uptime monitoring, release process.

### Sprint 8 (≈ Feb 2–13) — The went-there loop
The retention feature *and* the data flywheel: the day after an event, ask "did you go? rate it."
- **A:** Screenshot/OCR capture hardened against real pasted images.
- **B:** Taste profile v1 — weight ranking by votes and *attended* events; measure whether it
  improves pick quality.
- **C:** Post-event prompt UX + weekly digest (`exporters/markdown_digest.py` already exists — wire
  it to a scheduler).
- **Nick:** Privacy: data retention policy, user data deletion, `/privacy` command.

### Sprint 9 (≈ Feb 16–27) — Beta with real groups
- Onboard **3–5 real friend-group Discord servers** (dogfood). This is your user data for the report.
- All tracks: instrument, watch, fix. Daily triage of real failures.
- **Deliverable:** First real-usage telemetry.

### Sprint 10 (≈ Mar 2–13) — Evidence round 2
- **B:** Full re-benchmark: reliability, accuracy, latency, ranking quality — vs. the Sprint 1
  baseline, on the locked test set.
- **C:** **Usability study round 2** (new participants) — compare SUS to round 1.
- **A / Nick:** Fix what beta surfaced.
- **Deliverable:** `docs/EVAL_REPORT_v2.md` — the before/after that anchors your final report.

### Sprint 11 (≈ Mar 16–27) — Depth in each track
Each owner picks the most impressive remaining item in their area. This is the "make it yours"
sprint — where individual sections get their standout result.

### Sprint 12 (≈ Mar 30–Apr 10) — Hardening & accessibility
- Security review round 2 (fix findings), dependency audit, WCAG pass, error-path polish.
- **Feature freeze at the end of this sprint.**

### Sprint 13 (≈ Apr 13–24) — Report, poster, rehearsal
- Final report assembly (each person writes their own section, Nick edits for coherence).
- Poster design; demo video v2; **rehearse the live demo three times with a recorded fallback.**

### Sprint 14 (≈ Apr 27–May 14) — Demo day & handoff
- Demo day. Tag `v1.0`. Publish the OSS release, README, and architecture docs.
- Individual contribution statements; final retrospective.

---

## Deliverables calendar (what gets handed in)

| When | Artifact | Owner |
|---|---|---|
| Week 0 | `docs/TEAM.md` charter, 3 working environments, 3 first tasks | All |
| Sprint 1 | `docs/BASELINE.md` (every current metric, with intervals) | All, Nick edits |
| Sprint 2 | SRS / requirements doc | Nick + all |
| Sprint 3 | `docs/EVAL_REPORT_v1.md` | B |
| Sprint 4 | Live staging URL, usability findings v1 | Nick, C |
| Sprint 5 | Load test + security review | Nick |
| Sprint 6 | Demo video, Semester 1 report, `v0.1` | All |
| Sprint 9 | Beta telemetry from real servers | All |
| Sprint 10 | `EVAL_REPORT_v2.md`, usability v2 | B, C |
| Sprint 13 | Poster, final report draft | All |
| Sprint 14 | Demo day, `v1.0`, contribution statements | All |

---

## Priority list (do these in this order)

**P0 — do first, everything depends on it**
1. Week 0 kickoff (3 working environments, 3 tracks, 3 first tasks)
2. Trustworthy scorecard: confidence intervals, train/dev/test split, locked test set
3. Finish the corpus: 16 `needs_review` rows + pending label fixes
4. Measured live capture pass rate on a clean network
5. Architecture doc + ADRs + SRS
6. Unblock authed capture: fresh burner or the paid-resolver fallback

**P1 — the substance of the grade**
7. Category round 6 (embedding classifier, train-only centroids)
8. Promo-rejection precision + the "is this a place?" grounding check
9. Onboarding UX + capture progress + manual-add fallback
10. Usability study round 1
11. Async job queue + admin API lockdown + staging deploy
12. Gatekept-reel recovery (comment scrape)
13. Geo-aware ranking

**P2 — depth and polish, cut first if behind**
14. TikTok live + YouTube Shorts
15. Taste profile + went-there loop
16. Web map companion
17. Weekly digest, `/catalog` v2
18. OCR hardening, Telegram port, growth work

> **Cut rule:** if you're behind, cut P2 entirely and protect the *evidence* items (P0 #2–#4 and
> the eval/usability reports). A capstone with three features and real measurements beats one with
> ten features and no numbers.

---

## Required reading (linked, per track)

### Everyone — Week 0
- [The Twelve-Factor App](https://12factor.net/) — config, dependencies, deployment (short, skim all 12)
- [Conventional Commits](https://www.conventionalcommits.org/) + [Semantic Versioning](https://semver.org/)
- [Architecture Decision Records](https://adr.github.io/) — the ADR format you'll use all year
- [The Scrum Guide](https://scrumguides.org/) — ~13 pages, defines the ceremonies
- Project's own: `README.md`, `docs/TEAM_TODO.md`, `docs/HANDOFF.md`
- [pytest docs — getting started](https://docs.pytest.org/en/stable/getting-started.html)

### Track A — Capture & Data Sources
- [Playwright for Python](https://playwright.dev/python/docs/intro) — how the current fetch works
- [Meta: Instagram Platform docs](https://developers.facebook.com/docs/instagram-platform) — the *official* API path
- [Instagram Terms of Use](https://help.instagram.com/581066165581870) — read before writing any scraper
- [MDN HTTP overview](https://developer.mozilla.org/en-US/docs/Web/HTTP) — retries, status codes, caching
- Project's own: `docs/IG_AUTH_INGESTION_PLAN.md` (your spec)

### Track B — Accuracy & Evaluation
- Project's own, first: `docs/ML_REVIEW_QUESTIONS.md` — the leakage finding and why the test set is small
- [Stanford IR Book — evaluation (precision/recall)](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html)
- [Wilson score interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval) — confidence intervals on accuracy at small n
- [McNemar's test](https://en.wikipedia.org/wiki/McNemar%27s_test) — telling a real before/after gain from noise on the same rows
- [Cohen's kappa](https://en.wikipedia.org/wiki/Cohen%27s_kappa) — agreement between two annotators
- [Chroma docs](https://docs.trychroma.com/) — the vector store
- [RAG paper (Lewis et al.)](https://arxiv.org/abs/2005.11401) — the retrieval pattern behind `/plan`

### Track C — Experience & User Research
- [discord.py docs](https://discordpy.readthedocs.io/en/stable/) + [Discord Developer Docs](https://discord.com/developers/docs/intro)
- [Discord UI components (buttons, modals, select menus)](https://discord.com/developers/docs/components/reference)
- [Nielsen's 10 Usability Heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [Thinking Aloud usability testing](https://www.nngroup.com/articles/thinking-aloud-the-1-usability-method/)
- [System Usability Scale (SUS)](https://measuringu.com/sus/) — the 10-question score you'll report
- [WCAG 2.2 Quick Reference](https://www.w3.org/WAI/WCAG22/quickref/) — accessibility checklist

### Nick — Architecture & Platform
- [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) — **LLM01 prompt injection is directly relevant**
- [Simon Willison — prompt injection series](https://simonwillison.net/series/prompt-injection/)
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) — use as your security checklist
- [Google SRE Book — Service Level Objectives](https://sre.google/sre-book/service-level-objectives/) and [Postmortem Culture](https://sre.google/sre-book/postmortem-culture/)
- [GitHub Actions docs](https://docs.github.com/en/actions) · [Docker Compose docs](https://docs.docker.com/compose/)
- [OpenTelemetry](https://opentelemetry.io/docs/) · [k6](https://grafana.com/docs/k6/latest/) or [Locust](https://docs.locust.io/)
- [FastAPI security](https://fastapi.tiangolo.com/tutorial/security/)

---

## Copy-paste prompt sections (for Claude Code / Codex / agents)

Beginners ship 5× faster with a good prompt *and* a rule that they must understand the result.
**House prompt template — every task starts from this:**

```
You are working in the SpotBot repo (Python, FastAPI, discord.py, ChromaDB, Ollama).
Read these files before changing anything: <paths>.
Goal: <one sentence>.
Constraints: match existing patterns; keep the offline test suite green
(`pytest -k "not live" -q`); add unit tests for new logic; do not change files
outside <my track's directories>.
Explain your approach in 3 sentences BEFORE writing code, then implement.
```

**Track A — reliability measurement**
```
Read test_ig_live.py and docs/IG_AUTH_INGESTION_PLAN.md. Build a measurement
script that runs all known reel URLs through the unauthenticated path (and the
authenticated path when IG_USERNAME is set), and outputs a table: URL, path,
outcome (ok/login_wall/no_video/timeout/no_venue), latency. Write results to
docs/BASELINE.md. Do not change pipeline code — measurement only.
```

**Track B — confidence intervals on the scorecard**
```
Read src/ingestion/eval.py and docs/ML_REVIEW_QUESTIONS.md (findings 2 and 3).
Add a 95% Wilson confidence interval next to every metric eval.py prints for
--split test, and a --compare <baseline.json> mode that runs McNemar's test on
row-level correctness against a saved earlier run. Do not change the parser.
Add unit tests for the interval math and the comparison.
```

**Track C — onboarding wizard**
```
Read app/bot.py and app/cards.py. Add a /setup slash command: a Discord modal
wizard that (1) picks or creates a drop channel, (2) sets the guild's home city,
(3) saves via the admin API. Match the existing DynamicItem button patterns in
cards.py so it survives restarts. Add tests to app/test_cards.py.
```

**Nick — architecture**
```
Read src/ingestion/pipeline/orchestrator.py and app/bot.py::handle_reel_capture.
Capture is currently a synchronous HTTP call with a long timeout. Design an async
job queue (enqueue → worker → bot edits its status message). Write it as an ADR in
docs/adr/ with 2 alternatives considered and a decision, BEFORE implementing.
```

---

## Recommended inclusions (what makes a capstone score well)

**Include these — they're cheap and disproportionately impressive:**
- **The leakage story, told up front** — 49.6% → 37.4%, found and fixed in reporting, not hidden.
  It's the kind of result an advisor trusts the rest of the work more for hearing first.
- **ADRs** (`docs/adr/0001-*.md`) — shows engineering judgment, not just coding
- **A before/after metrics table with intervals** — the single most persuasive slide you will have
- **A negative result, written up** — the local LLM made extraction *worse* on CPU, measured twice
- **A risk register** with what actually went wrong and how you responded
- **Individual contribution statements** — protects each of you in grading
- **A 2–3 minute demo video** with a recorded fallback (never live-demo without one)
- **A security & ethics section**: IG ToS position (user-initiated paste only, never crawling),
  prompt-injection defense, PII handling, data deletion
- **A public artifact** — the OSS repo + the shareable catalog page. Something graders can click.

**Discuss with your advisor early:**
- Whether your user study needs IRB review (class projects are often exempt, but *ask* — don't assume)
- Whether the scraping approach is acceptable, or whether they'd prefer the official Meta API path
- Whether a measured heuristic parser counts as the ML contribution, or a trained model is expected
  (`docs/ML_REVIEW_QUESTIONS.md` question 11)

**Deliberately skip:** payments, mobile apps, custom auth systems, microservices, Kubernetes.
Every one of those eats a sprint and adds nothing to the grade.

---

## Rituals & definition of done

- **Standup:** async in Discord, 3 lines (did / doing / blocked), Mon/Wed/Fri
- **Sprint planning:** 45 min, first meeting of the sprint; each owner commits to 2–4 items
- **Sprint review:** 45 min, end of the sprint; **each person demos their own work**
- **Retro:** 15 min after review; one thing to start/stop/continue
- **Pair rotation:** one 45-min pairing session per sprint across track boundaries — prevents silos
  and means two people can answer questions about every subsystem at demo day

**Definition of done:** merged to main · one non-author approval · tests added · offline suite
green in CI · docs/ADR updated if behavior changed · demoed at review · owner can explain it.

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Nick becomes the bottleneck / does everyone's work** | High | Anti-bottleneck rules above; Nick's own sprint capacity capped at ~50% feature work |
| **Beginners stall silently** | High | Week 0 kickoff; 1-day stuck rule; pairing; tiny-PR ramp before real features |
| **The test set gets burned by repeated tuning** | High | Tune on train, report test rarely; a locked test set compared only at evidence rounds |
| **Instagram breaks capture mid-semester** | Medium-High | Fallback chain; paid resolver behind the same interface; the *measurement* work survives either way |
| **Scope creep from the product roadmap** | High | The P0/P1/P2 cut rule; roadmap growth work is explicitly out of scope |
| **Campus network TLS interception** | Known issue | Install at home; documented workarounds (`truststore`, `BOT_INSECURE_SSL`, hotspot) |
| **No GPU — local chat LLMs are slow and less accurate** | Certain (measured: 30–56s/call) | Heuristic-first extraction, Ollama for embeddings; hosted-LLM go/no-go on low-confidence rows only |
| **Everyone waits until the last month** | Medium | Hard deliverable at the end of *every* sprint, not just semester end |

---

## Verification — how we know the plan is working

- **Every sprint:** a demo by each owner + a merged PR from each person. If someone has zero
  merged PRs in a sprint, that's the retro topic.
- **Sprint 1 and Sprint 10:** the same benchmark run on the same locked test set, with intervals.
  The delta between them *is* the capstone result.
- **CI:** full offline suite green on every PR (`pytest -k "not live" -q`); accuracy floors ratchet
  on `--split train`.
- **Semester 1 exit:** live staging URL, `BASELINE.md` + `EVAL_REPORT_v1.md` + usability v1, `v0.1` tagged.
- **Semester 2 exit:** production deploy with ≥3 real servers using it, `EVAL_REPORT_v2.md` showing
  before/after, poster + report + `v1.0`.

## Next actions
1. Week 0 kickoff meeting
2. Turn the P0 items into GitHub issues, one owner each
3. Book time with the ML advisor using `docs/ML_REVIEW_QUESTIONS.md`
