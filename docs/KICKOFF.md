# Kickoff handoff — Thursday, September 17, 2026

For everyone joining the SpotBot capstone. Read this before the meeting; it takes ten
minutes. Everything here is on `main` and was verified on 2026-09-16.

The plan itself is at https://nicknojiri.github.io/SocialAgent-Team10/ (same content as
`docs/CAPSTONE_PLAN.md`). This page is the "what do I actually do" version.

---

## 1. What this is, in three sentences

SpotBot is a Discord bot. You paste an Instagram reel of a restaurant or hangout spot;
it reads the caption and the spoken audio, works out the venue, and posts a card your
friends vote on — and `/plan` turns a group chat into an actual outing. It already
works; the capstone is making it *reliable* and *proving that with numbers*.

## 2. Get it running (at home, not on campus Wi-Fi — it intercepts downloads)

1. Install **Python 3.11+** (tick "Add python.exe to PATH"), **Git**, **VS Code**, and
   **Ollama** (https://ollama.com/download).
2. In PowerShell:
   ```powershell
   git clone https://github.com/NickNojiri/SocialAgent-Team10.git
   cd SocialAgent-Team10
   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
   ```
   The setup pulls two Ollama models — `mxbai-embed-large` (required, ~0.7 GB) and
   `llama3.1:8b` (optional, ~5 GB). Start it early.
   **Mac/Linux:** `make setup`, then use `.venv/bin/python` wherever this doc says
   `.\.venv\Scripts\python.exe`.
3. Prove it works — no Discord account or token needed:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest -k "not live" -q
   .\.venv\Scripts\python.exe -m src.ingestion.eval --offline --split test
   ```
   First one: **253 passed**. Second one prints the scorecard; the line to look at is
   `venue exact : 37.4%`. That number is what this whole project is about.

If a step fails, screenshot the error and bring it — we fix setup together first thing.

## 3. Where it actually stands (the honest version)

| Thing | State |
|---|---|
| Capture (reel → card) | works; 13/13 on a hand-picked set in July, **never measured on random reels** |
| Venue name correct | **37.4%** on reels the parser was never tuned on (was reported 49.6% until we found our own test labels had leaked into the scoring — fixed) |
| Category correct | 63.2%, only 8.5 points better than always guessing "food & drink" |
| Promo rejection | throws away a real place half the time it rejects something |
| Tests | 253 offline, run on every push (GitHub Actions) |
| Local LLM | measured to make extraction *worse* on CPU; it is a gap-filler, not the extractor |

The extractor gets the venue right about half as often as the information in the
input allows (the name is literally present in 78% of rows). That gap is the work.

## 4. Pick a track

| Track | You own | First measurable claim |
|---|---|---|
| **A — Capture** | fetching reels reliably, more platforms | "the live pass rate on N random reels is X%" |
| **B — Accuracy** | the label corpus, the scorecard, the parser | "venue went 37% → Y% and here is the paired test" |
| **C — Experience** | the Discord UX, the web page, user studies | "time-to-card fell from X to Y; SUS rose to Z" |
| Nick | architecture, deployment, code review, the demo | — |

Choose by interest. Anyone can swap after two weeks. Full descriptions, reading lists
and starter prompts: the plan page, sections "Who owns what" and "Required reading".

## 5. Your first task (pick the one for your track)

All commands run from the repo root. Every PR needs one approval from a teammate;
nobody merges their own.

### Track A — measure the live capture pass rate
`test_ig_live.py` has 13 reel URLs; `docs/REEL_TRANSCRIBE_TEST_PLAN.md` lists 6 more.
Add those 6 to `IG_URLS` (19 total). On home Wi-Fi:
```powershell
.\.venv\Scripts\python.exe -m pytest test_ig_live.py -v -s --tb=short
```
Write `docs/BASELINE.md`: one row per URL — link · result (ok / login wall / no video /
timeout / no venue) · rough time. Measurement only; don't change pipeline code.
**Done when:** PR with the 6 URLs and `BASELINE.md`, approved.

### Track B — apply the two pending label fixes and re-run the scorecard
Both rows are in `fixtures/labels.jsonl` (one JSON object per line — keep it that way;
don't run `scripts\review.py` while anything else writes the file):
- `DYh-C2FPiGS`: gold category `nightlife` → `cafe_dessert`; gold venue `Thef1rsttake` → `The First Take`
- `DZzvbTupPND`: note → `name_in_comments`

```powershell
.\.venv\Scripts\python.exe -m src.ingestion.eval --offline --split test   # before
# edit the two rows
.\.venv\Scripts\python.exe scripts\build_aliases.py                        # rebuild both alias tables
.\.venv\Scripts\python.exe -m src.ingestion.eval --offline --split test   # after
.\.venv\Scripts\python.exe -m pytest test_extraction_labels.py -q
```
Both rows are in the *train* split, so the held-out numbers should not move — say in
the PR whether they did, and why.
**Done when:** PR with the labels, the regenerated alias tables, and before/after
numbers, approved.

### Track C — add the multi-link routing test
Read `app/test_bot.py`; `TestOnMessageRouting` already covers single links, TikTok,
muted channels, bot authors and DMs. Add one test: a message with two different reel
links plus a repeat of the first must call `handle_reel_capture` **once**, with both
URLs and the repeat dropped. For text containing
`https://www.instagram.com/reel/AAA111/?igsh=x`, `https://instagram.com/p/BBB222`, and
the first link again, the call receives
`['https://www.instagram.com/reel/AAA111/', 'https://instagram.com/p/BBB222/']`.
```powershell
.\.venv\Scripts\python.exe -m pytest app\test_bot.py -q
```
**Done when:** PR with the passing test, approved. Next: measure time-to-card
(p50/p95) for the "before" UX.

## 6. How we work

- **Standup** Mon/Wed/Fri, three lines in Discord: did · doing · blocked.
- **Stuck for more than a day → say so.** Nick pairs for 45 minutes; you type.
- **Every PR:** one non-author approval, tests added, offline suite green in CI,
  docs updated if behaviour changed. You must be able to explain any code you ship —
  AI tools are encouraged, code you can't explain doesn't merge.
- **Sprint review** every two weeks: each person demos their own work.
- Sprint 1 runs Sept 15–26. Sprint plan, dates and deliverables: the plan page.

## 7. Where things are

| Want to… | Go to |
|---|---|
| understand the system | `docs/ARCHITECTURE.md` (start here), then `docs/PIPELINE.md` |
| know why it's built this way | `docs/adr/` |
| see what counts as done | `docs/SRS.md` |
| see the ranked backlog | `docs/TEAM_TODO.md` |
| run it day to day | `docs/RUNBOOK.md`, `docs/SETUP_GUIDE.md` |
| run the bot on Discord (Sprint 1) | `docs/RUN_AT_WORK.md` — needs a bot token; not for day one |
| understand the accuracy work (Track B) | `docs/EXTRACTION_ACCURACY.md`, `docs/HANDOFF.md`, `docs/ML_REVIEW_QUESTIONS.md` |
| know the security state | `docs/THREAT_MODEL.md` |
| use the bot as a member | `docs/USER_GUIDE.md` |

## 8. Gotchas we already hit, so you don't

- **"running scripts is disabled"** on Windows → use the `powershell -ExecutionPolicy
  Bypass -File …` form above; it changes no system setting.
- **Campus / corporate Wi-Fi** breaks downloads and `git push` (TLS interception).
  Install at home; push with `git -c http.sslVerify=false push` if you must.
- **Ollama must be running** for anything that embeds (`ollama serve` if the tray
  app isn't up). Tests and the scorecard do *not* need it.
- **`.env` and `data/` are never committed.** The Discord token lives only in `.env`.
- **`main` is the only branch you need.** Older `feature/*` and `claude/*` branches are
  history; don't check them out.
- First live capture is slow (~40 s) — Whisper downloads its model once.

## 9. What Nick is doing this sprint

Architecture doc, ADRs, SRS and threat model are done (see §7). Open: commit the
tenant-authorization fix, lock down the admin API on the always-on box, book the ML
advisor with `docs/ML_ADVISOR_BRIEF.md`, and run the async capture queue on real
captures.

Questions before Thursday: Discord, or reply on Canvas.
