# SpotBot — CSULB Capstone Sprint Plan (Fall 2026 → Spring 2027)

## Context

SpotBot is a working Discord bot: paste an Instagram reel → it transcribes the audio (Whisper),
extracts venue/category/time (local LLM), geocodes it, and posts a votable "spot card." `/plan`
reads a group chat and proposes an outing; who's-in votes hit quorum and create a Discord
Scheduled Event. ~15 test files, CI, a public share page, and a product roadmap
(`docs/PRODUCT_ROADMAP.md`) already exist.

**Two facts drive this entire plan:**

1. **Nick built ~all of it.** In a capstone, "one person did the work" is a grading problem *and*
   a burnout problem. Scope must be carved so three other people own real, separable subsystems.
2. **The teammates are beginners.** So every track needs a ramp ladder (read → tiny PR → real
   feature), a module boundary that can't break the critical path, and copy-paste agent prompts.

**The reframe that makes this a capstone instead of a hackathon project:** stop measuring success
in features and start measuring it in **evidence**. The project already has the single best
capstone hook available — a known, unmeasured quality gate: *unauthenticated Instagram capture
succeeds only ~40–70% of the time.* Turning that into a measured, improved, defended number is
worth more than five new features.

**Outcome we're aiming at:** a deployed, multi-tenant system with a published reliability and
accuracy benchmark, a real user study, and four people who can each stand up and defend a
subsystem they personally own.

> ⚠️ Confirm every date against your 491A/491B syllabus and academic calendar — the week ranges
> below are estimates. Confirm deliverable formats (SRS template, poster size, report length)
> with your advisor in Week 1; they override anything here.

---

## Team structure — four individual sections

Each person owns a **named subsystem**, a **measurable claim**, and a **section of the final
report**. Nobody is "helping." Ownership means: you write it, you test it, you demo it, you
answer questions about it.

| Person | Track | Owns | The claim they defend at demo day |
|---|---|---|---|
| **Nick** | **Architecture & Integration** | Pipeline orchestration, async job queue, release engineering, code review, the demo | "I designed the ingestion architecture and made capture non-blocking at N concurrent captures." |
| **Teammate 1** | **A — Capture & Data Sources** | `sources/ig_authed.py`, fallback/retry, failure taxonomy, 2nd platform (TikTok) | "I raised capture success from X% to Y% on N real posts and added a second source." |
| **Teammate 2** | **B — Intelligence & Evaluation** | Golden dataset, eval harness, extraction/STT accuracy, hallucination guard, ranking | "I built the benchmark; venue precision went X→Y and hallucinated venues dropped Z%." |
| **Teammate 3** | **C — Experience & User Research** | Discord UX, onboarding, web companion + map, accessibility, usability study | "I ran a study with N users; time-to-first-card fell from X to Y and SUS rose to Z." |
| **Teammate 4** | **D — Platform, Security & Ops** | Multi-tenancy, authn/z, CI/CD, deployment, observability, security & ethics review | "I deployed it multi-tenant, took CI from 1 to 15 suites, and led the threat model." |

> **If you only have 4 people total (you + 3):** drop Track D's scope into Nick's track and give
> Track D's *security & ethics review* to Teammate 2 (it pairs naturally with evaluation). Or run
> A/B/C as the three teammate tracks and Nick takes Architecture + Platform. Recommended: **Nick =
> Architecture + Platform**, teammates take A, B, C.

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
- **B** lives in a new `eval/` directory + `pipeline/llm_extractor.py`, `summarizer.py`,
  `serving/recommender.py`.
- **C** lives in `app/` (Discord) + `serving/admin.py` templates.
- **D** lives in `serving/tenant_auth.py`, `.github/workflows/`, deployment config, `docker-compose.yml`.
- **Nick** owns `pipeline/orchestrator.py` and anything touching two tracks.

---

## Week 0 — Bootcamp (before Sprint 1). Everyone, together.

The single highest-leverage two days in the whole project. Goal: **every teammate runs the full
stack locally and merges one tiny PR.** A beginner who has shipped once behaves completely
differently from one who hasn't.

- [ ] Everyone installs Python 3.11+, Git, Ollama, VS Code
- [ ] Clone → `git checkout feature/reel-capture` → `.\scripts\setup.ps1` (already written)
- [ ] Everyone gets their **own** Discord bot token + a shared test server; runs `run_local.ps1`
- [ ] Everyone captures one reel and sees their own card appear
- [ ] **Tiny PR each** (pre-picked by Nick, ~5 lines): add a category emoji, fix a docstring, add
      one assert to a test. Branch → PR → review → merge. The point is the *ritual*, not the code.
- [ ] Read together: `README.md`, `docs/PIPELINE.md`, `docs/PRODUCT_ROADMAP.md`
- [ ] Agree on: sprint cadence, standup channel, definition of done, commit convention

**Deliverable:** 4 merged PRs, 4 working local environments, a team charter in `docs/TEAM.md`
(roles, cadence, decision process, how disagreements resolve).

---

## Semester 1 — Fall 2026 (~13 weeks, 6 sprints). Theme: **"Prove it works."**

Grade target: a *measured* baseline, a real architecture, and a working demo.

### Sprint 1 (≈ Sept 15–26) — Baseline & the go/no-go number
The most important sprint of the semester. You cannot improve what you haven't measured.
- **A:** Run the existing `test_ig_live.py` harness on all 19 known URLs, unauthenticated, on a
  clean network. **Publish the real pass rate.** Build a failure taxonomy (login wall / no video /
  timeout / no venue).
- **B:** Start the **golden dataset**: 60 reels, hand-labeled (venue name, city, category,
  has-speech). Two people label 20 of the same ones to measure agreement.
- **C:** Instrument and measure **time-to-card** today (p50/p95). Record a screen capture of the
  current UX as the "before."
- **D:** **Fix CI** — it currently runs only `test_ingestion.py` out of 15 test files. Make it run
  the whole offline suite, add a coverage report and a badge.
- **Nick:** Architecture diagram + first 3 ADRs (why Chroma, why local LLM, why Discord-first).
- **Deliverable:** `docs/BASELINE.md` — every current number in one table.

### Sprint 2 (≈ Sept 29–Oct 10) — Requirements & reliability attempt 1
- **A:** Get `AuthedInstagramSource` working live; measure the authed pass rate vs. baseline.
- **B:** Golden set → 100 items; write the eval runner (`eval/run_eval.py`) producing
  precision/recall for venue + category.
- **C:** `/setup` onboarding wizard + live capture progress message (replaces the silent 20–40s wait).
- **D:** Finish `guild_id` scoping end-to-end; move channel config out of `channels.json` into the DB.
- **Nick:** **SRS / requirements doc** (functional + non-functional, with the measured numbers as
  NFR targets). Everyone contributes their track's requirements.
- **Deliverable:** SRS v1 submitted.

### Sprint 3 (≈ Oct 13–24) — Accuracy & the hallucination problem
- **A:** Retry + fallback chain (authed → unauthenticated → embed → manual). Failure taxonomy v2.
- **B:** **The "is this even a place?" filter.** Real motivating example already in the data: a
  *Modern Family* comedy clip was force-fit into a venue, and one summary invented a venue name
  ("Seneca Downtown") that was never said. Add an `is_vague`/not-a-place gate + a grounding check
  that rejects venue names absent from caption *and* transcript. Measure hallucination rate before/after.
- **C:** Manual-add modal for failed captures + error-state copy. Recruit user-study participants.
- **D:** Secrets handling audit, rate limiting, and the **prompt-injection threat model** (reel
  captions are untrusted text going into an LLM — OWASP LLM01).
- **Nick:** Async job queue design doc + spike.
- **Deliverable:** `docs/EVAL_REPORT_v1.md` with real accuracy numbers.

### Sprint 4 (≈ Oct 27–Nov 7) — Make it usable by strangers
- **A:** Transcription speed work (conditional transcription — skip Whisper when the caption
  already names venue+location; smallest video variant; VAD; thread tuning). Target ~2–3× faster.
- **B:** Geo-aware ranking — lat/lng are already stored but unused in `recommender.py`. Rank by
  distance from the guild's home city + popularity.
- **C:** **Usability study round 1** — 6–8 participants, think-aloud, SUS questionnaire.
- **D:** Deploy a staging instance to a real host. Add structured logging + a metrics endpoint.
- **Nick:** Implement the job queue; capture becomes non-blocking.
- **Deliverable:** Live staging URL + usability findings.

### Sprint 5 (≈ Nov 10–21) — Integrate & harden
- **A:** TikTok extractor (mirrors `extractors/instagram.py` — good pattern-copy task).
- **B:** Eval round 2 on the improved pipeline; charts for the report.
- **C:** Act on study findings (top 3 fixes); accessibility pass (contrast, alt text, screen reader).
- **D:** Load test (k6/Locust) — how many concurrent captures before it degrades? Security review doc.
- **Nick:** Integration freeze; all four tracks working together on staging.
- **Deliverable:** Load test + security review docs.

### Sprint 6 (≈ Nov 24–Dec 12) — Midterm demo & write-up
- Feature freeze at the start of this sprint. **Bugs and docs only.**
- Record a 3-minute demo video (paste reel → card → `/plan` → scheduled event).
- **Semester 1 report** — each person writes their own section.
- Retrospective + Semester 2 backlog groomed.
- **Deliverable:** Demo + report + tagged release `v0.1`.

**Semester 1 exit criteria:** measured capture reliability, a published eval report, a live staging
deployment, a completed user study, CI green on the full suite, four people who each shipped.

---

## Semester 2 — Spring 2027 (~17 weeks, 8 sprints). Theme: **"Make it real."**

Grade target: a deployed product with real users, a second round of evidence, and a polished defense.

### Sprint 7 (≈ Jan 19–30) — Production readiness
- **A:** Source reliability SLO; alerting when pass rate drops below target.
- **B:** Taste profile v1 — weight by votes and *attended* events; category diversification.
- **C:** Web companion: map view with spots plotted (OSM tiles), category filters.
- **D:** Production deploy, backups, secrets rotation, uptime monitoring.
- **Nick:** Release process, versioning, rollback plan.

### Sprint 8 (≈ Feb 2–13) — The went-there loop
The retention feature *and* the data flywheel: the day after an event, ask "did you go? rate it."
- **A:** Screenshot/OCR capture source (stretch) or source hardening.
- **B:** Feed attendance data into ranking; measure whether it improves pick quality.
- **C:** Post-event prompt UX + weekly digest (`exporters/markdown_digest.py` already exists — wire
  it to a scheduler).
- **D:** Privacy: data retention policy, user data deletion, `/privacy` command.

### Sprint 9 (≈ Feb 16–27) — Beta with real groups
- Onboard **3–5 real friend-group Discord servers** (dogfood). This is your user data for the report.
- All tracks: instrument, watch, fix. Daily triage of real failures.
- **Deliverable:** First real-usage telemetry.

### Sprint 10 (≈ Mar 2–13) — Evidence round 2
- **B:** Full re-benchmark: reliability, accuracy, latency, ranking quality — vs. the Sprint 1 baseline.
- **C:** **Usability study round 2** (new participants) — compare SUS to round 1.
- **A/D:** Fix what beta surfaced.
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
| Week 0 | `docs/TEAM.md` charter + 4 merged PRs | All |
| Sprint 1 | `docs/BASELINE.md` (every current metric) | All, Nick edits |
| Sprint 2 | SRS / requirements doc | Nick + all |
| Sprint 3 | `docs/EVAL_REPORT_v1.md` | B |
| Sprint 4 | Live staging URL, usability findings v1 | D, C |
| Sprint 5 | Load test + security review | D |
| Sprint 6 | Demo video, Semester 1 report, `v0.1` | All |
| Sprint 9 | Beta telemetry from real servers | All |
| Sprint 10 | `EVAL_REPORT_v2.md`, usability v2 | B, C |
| Sprint 13 | Poster, final report draft | All |
| Sprint 14 | Demo day, `v1.0`, contribution statements | All |

---

## Priority list (do these in this order)

**P0 — do first, everything depends on it**
1. Week 0 bootcamp (4 working environments + 4 merged PRs)
2. Measured baseline: capture pass rate, time-to-card, accuracy (Sprint 1)
3. CI running the full test suite (currently 1 of 15 files)
4. Golden dataset (100 labeled reels) — nothing in Track B works without it
5. Architecture doc + ADRs + SRS
6. Authed capture reliability → the go/no-go number

**P1 — the substance of the grade**
7. Hallucination guard + "is this a place?" filter
8. Multi-tenancy finished + deployed staging
9. Onboarding UX + capture progress + manual-add fallback
10. Usability study round 1
11. Async job queue (non-blocking capture)
12. Geo-aware ranking
13. Security/threat model + prompt-injection defense

**P2 — depth and polish, cut first if behind**
14. TikTok / second capture source
15. Taste profile + went-there loop
16. Web map companion
17. Weekly digest, `/catalog` v2
18. OCR/screenshot capture, Telegram port, growth work

> **Cut rule:** if you're behind, cut P2 entirely and protect the *evidence* items (P0 #2, #4 and
> the eval/usability reports). A capstone with three features and real measurements beats one with
> ten features and no numbers.

---

## Required reading (linked, per track)

### Everyone — Week 0
- [The Twelve-Factor App](https://12factor.net/) — config, dependencies, deployment (short, skim all 12)
- [Conventional Commits](https://www.conventionalcommits.org/) + [Semantic Versioning](https://semver.org/)
- [Architecture Decision Records](https://adr.github.io/) — the ADR format you'll use all year
- [The Scrum Guide](https://scrumguides.org/) — ~13 pages, defines the ceremonies
- Project's own: `README.md`, `docs/PIPELINE.md`, `docs/PRODUCT_ROADMAP.md`
- [pytest docs — getting started](https://docs.pytest.org/en/stable/getting-started.html)

### Track A — Capture & Data Sources
- [Playwright for Python](https://playwright.dev/python/docs/intro) — how the current fetch works
- [Meta: Instagram Platform docs](https://developers.facebook.com/docs/instagram-platform) — the *official* API path
- [Instagram Terms of Use](https://help.instagram.com/581066165581870) — read before writing any scraper
- [HTTP caching & retries: MDN HTTP overview](https://developer.mozilla.org/en-US/docs/Web/HTTP)
- Project's own: `docs/IG_AUTH_INGESTION_PLAN.md` (your spec)

### Track B — Intelligence & Evaluation
- [Stanford IR Book — evaluation (precision/recall)](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html)
- [Word Error Rate](https://en.wikipedia.org/wiki/Word_error_rate) + [jiwer](https://github.com/jitsi/jiwer) to compute it
- [Whisper paper (Radford et al.)](https://arxiv.org/abs/2212.04356) — how the STT model works
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — the implementation you're using
- [RAG paper (Lewis et al.)](https://arxiv.org/abs/2005.11401) — the retrieval pattern behind `/plan`
- [Chroma docs](https://docs.trychroma.com/) — the vector store
- [Ragas](https://docs.ragas.io/) — evaluation metrics for LLM pipelines (faithfulness = your hallucination metric)
- [Cohen's kappa](https://en.wikipedia.org/wiki/Cohen%27s_kappa) — inter-annotator agreement on the golden set

### Track C — Experience & User Research
- [discord.py docs](https://discordpy.readthedocs.io/en/stable/) + [Discord Developer Docs](https://discord.com/developers/docs/intro)
- [Discord UI components (buttons, modals, select menus)](https://discord.com/developers/docs/components/reference)
- [Nielsen's 10 Usability Heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [Thinking Aloud usability testing](https://www.nngroup.com/articles/thinking-aloud-the-1-usability-method/)
- [System Usability Scale (SUS)](https://measuringu.com/sus/) — the 10-question score you'll report
- [WCAG 2.2 Quick Reference](https://www.w3.org/WAI/WCAG22/quickref/) — accessibility checklist

### Track D — Platform, Security & Ops
- [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) — **LLM01 prompt injection is directly relevant**
- [Simon Willison — prompt injection series](https://simonwillison.net/series/prompt-injection/)
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) — use as your security checklist
- [Google SRE Book — Service Level Objectives](https://sre.google/sre-book/service-level-objectives/) and [Postmortem Culture](https://sre.google/sre-book/postmortem-culture/)
- [GitHub Actions docs](https://docs.github.com/en/actions)
- [Docker Compose docs](https://docs.docker.com/compose/)
- [OpenTelemetry](https://opentelemetry.io/docs/) — tracing/metrics
- [k6 load testing](https://grafana.com/docs/k6/latest/) or [Locust](https://docs.locust.io/)
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
(`pytest -q --ignore=test_ig_live.py`); add unit tests for new logic; do not
change files outside <my track's directories>.
Explain your approach in 3 sentences BEFORE writing code, then implement.
```

**Track A — reliability measurement**
```
Read test_ig_live.py and docs/IG_AUTH_INGESTION_PLAN.md. Build a measurement
script that runs all known reel URLs through both the unauthenticated and the
authenticated path, and outputs a table: URL, path, outcome (ok/login_wall/
no_video/timeout/no_venue), latency. Write results to docs/BASELINE.md.
Do not change pipeline code — measurement only.
```

**Track B — golden set + eval harness**
```
Create eval/: a golden.jsonl of hand-labeled reels (url, true_venue, true_city,
true_category, has_speech) and run_eval.py that runs the pipeline over them and
reports precision/recall for venue and category, plus a hallucination rate
(venue name that appears in NEITHER caption nor transcript). Print a summary
table and write eval/results/<date>.json. Add unit tests with a fake pipeline.
```

**Track C — onboarding wizard**
```
Read app/bot.py and app/cards.py. Add a /setup slash command: a Discord modal
wizard that (1) picks or creates a drop channel, (2) sets the guild's home city,
(3) saves via the admin API. Match the existing DynamicItem button patterns in
cards.py so it survives restarts. Add tests to app/test_cards.py.
```

**Track D — CI and security**
```
Read .github/workflows/ci.yml. It runs only test_ingestion.py, but the repo has
15 test files. Rewrite CI to run the full offline suite with coverage reporting,
fail under 70% coverage, and cache the Playwright browser. Then audit
src/ingestion/serving/tenant_auth.py and list every endpoint that is not
authenticated, as a markdown table in docs/SECURITY_REVIEW.md.
```

**Nick — architecture**
```
Read src/ingestion/pipeline/orchestrator.py and app/bot.py::handle_reel_capture.
Capture is currently a synchronous 180s HTTP call. Design an async job queue
(enqueue → worker → bot edits its status message). Write it as an ADR in
docs/adr/ with 2 alternatives considered and a decision, BEFORE implementing.
```

---

## Recommended inclusions (what makes a capstone score well)

**Include these — they're cheap and disproportionately impressive:**
- **ADRs** (`docs/adr/0001-*.md`) — shows engineering judgment, not just coding
- **A before/after metrics table** — the single most persuasive slide you will have
- **A risk register** with what actually went wrong and how you responded
- **Individual contribution statements** — protects each of you in grading
- **A 2–3 minute demo video** with a recorded fallback (never live-demo without one)
- **Test coverage badge** + green CI on the README
- **A security & ethics section**: IG ToS position (user-initiated paste only, never crawling),
  prompt-injection defense, PII handling, data deletion. Modern capstones reward this heavily and
  most teams skip it entirely.
- **A public artifact** — the OSS repo + the shareable catalog page. Something graders can click.

**Discuss with your advisor early:**
- Whether your user study needs IRB review (class projects are often exempt, but *ask* — don't assume)
- Whether the scraping approach is acceptable, or whether they'd prefer the official Meta API path.
  Have a written answer ready either way; "we chose user-initiated paste only, never crawling, and
  here's our ToS analysis" is a strong answer.

**Deliberately skip:** payments, mobile apps, custom auth systems, microservices, Kubernetes.
Every one of those eats a sprint and adds nothing to the grade.

---

## Rituals & definition of done

- **Standup:** async in Discord, 3 lines (did / doing / blocked), Mon/Wed/Fri
- **Sprint planning:** 45 min, first Monday of the sprint; each owner commits to 2–4 items
- **Sprint review:** 45 min, last Friday; **each person demos their own work**
- **Retro:** 15 min after review; one thing to start/stop/continue
- **Pair rotation:** one 45-min pairing session per sprint across track boundaries — prevents silos
  and means two people can answer questions about every subsystem at demo day

**Definition of done:** merged to main · one non-author approval · tests added · offline suite
green in CI · docs//ADR updated if behavior changed · demoed at review · owner can explain it.

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Nick becomes the bottleneck / does everyone's work** | High | Anti-bottleneck rules above; Nick's own sprint capacity capped at ~50% feature work |
| **Beginners stall silently** | High | Week 0 bootcamp; 1-day stuck rule; pairing; tiny-PR ramp before real features |
| **Instagram breaks the scraper mid-semester** | Medium-High | Fallback chain; paid resolver behind the same interface; the *measurement* work survives either way |
| **Scope creep from the product roadmap** | High | The P0/P1/P2 cut rule; roadmap Phase 3 (growth) is explicitly out of scope |
| **Campus network TLS interception blocks everything** | Known issue | Documented workarounds (`truststore`, `BOT_INSECURE_SSL`, hotspot) in the env notes |
| **No GPU — transcription is slow** | Certain | Conditional transcription + smallest-variant download (Sprint 4); document as a constraint, not a failure |
| **Everyone waits until the last month** | Medium | Hard deliverable at the end of *every* sprint, not just semester end |

---

## Verification — how we know the plan is working

- **Every sprint:** a demo by each owner + a merged PR from each person. If someone has zero
  merged PRs in a sprint, that's the retro topic.
- **Sprint 1 and Sprint 10:** the same benchmark run, producing comparable numbers. The delta
  between them *is* the capstone result.
- **CI:** full offline suite green on every PR (`pytest -q --ignore=test_ig_live.py`), coverage
  trending up.
- **Semester 1 exit:** live staging URL, `BASELINE.md` + `EVAL_REPORT_v1.md` + usability v1, `v0.1` tagged.
- **Semester 2 exit:** production deploy with ≥3 real servers using it, `EVAL_REPORT_v2.md` showing
  before/after, poster + report + `v1.0`.

## First three actions after approval
1. Publish this plan as a shareable page for the team (artifact link) + commit as `docs/CAPSTONE_PLAN.md`
2. Create the GitHub Project board with the 14 sprints and P0 items as issues
3. Schedule Week 0 bootcamp and send the Week 0 reading list
