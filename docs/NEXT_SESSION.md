# Next Session Plan: Integration & Live Validation

Here is the concrete, time-boxed plan for the next ~5-hour session. It is ordered so that critical deliverables are shipped first, with maximum buffer reserved for the riskiest integration (Discord bot).

## Prerequisites (Before starting)
- 📶 **Hotspot / clean network** (Required for push, CI, and live geocoding — campus TLS blocks all three).
- 🐳 **Docker running** + a **`DISCORD_TOKEN`** in `.env` (Required for the bot block).
- Repo active on the `ingestion-engine` branch, with the `venv` active.

---

## Hour 0:00–0:30 — Ship it to GitHub
*The local commits exist only on this machine right now — get them safe first.*
- Run `git status` to ensure tree is clean, then `pytest -k "not live" -q` (should be green, ~113 tests).
- Decide on `.claude/`: commit the project `launch.json` or add `.claude/` to `.gitignore`.
- **On hotspot:** `git push origin ingestion-engine`
- Confirm GitHub Actions CI kicks off.
- **Checkpoint:** Branch pushed, CI running.

## Hour 0:30–1:15 — CI Green + PR
- Watch the CI run. Fix any runner-specific failures (most likely the `playwright install` step or a path).
  - *Claude Code* triages; *Codex* patches if it's a code issue.
- Open PR `ingestion-engine → main` with a description summarizing Phases 1–7 (pull from `docs/PIPELINE.md`).
- **Checkpoint:** ✅ Green CI on an open PR.

## Hour 1:15–2:15 — Live Validation (First time on a clean network)
- Run the live geocoding test: `pytest -k "live" -q` (Nominatim now reachable) — confirm coordinates resolve.
- Run the full pipeline WITH geo on 3–5 real IG URLs: `python -m src.ingestion.cli "<url>" --chroma`
  - Confirm `geo=geocoded`, records validate, and explicitly note the **IG reliability rate** (how many hit a login wall) — this is the real-world robustness data point we need.
- **Checkpoint:** Phase 3 geo proven live; Chroma collection now has real data.

## Hour 2:15–3:30 — Discord Bot End-to-End (Biggest unknown, most buffer)
*This is delivered but never live-run — expect to debug.*
- Run `docker-compose up` (bot + llm + db + recommend), or run `recommend` service + bot locally.
- In Discord: use `/events late night tacos`, then `/suggestions on` and chat naturally.
- Verify recommendations post with the formatter (venue, `<t:>` time, source link).
- *Claude Code* debugs wiring; if it won't cooperate in time, **document the gap and move on** — don't let it eat the whole session.
- **Checkpoint:** Either a working `/events` screenshot, or a written list of what's broken.

## Hour 3:30–4:15 — Last Code Item + Polish
- **Claude Code:** The `Locations` text-isolation fix (now inspectable against live IG DOM) — the one remaining code nit.
- Fix anything small surfaced during live runs.
- `pytest -k "not live"` → commit → push.
- **Checkpoint:** Last code item closed.

## Hour 4:15–5:00 — Demo Prep + Wrap
- Generate a Markdown digest from the live data (using Codex's exporter): 
  `python -m src.ingestion.exporters.markdown_digest data/inspirations.jsonl` → a shareable artifact.
- Update `task.md` / `RUNBOOK.md` with live-validation results.
- **Antigravity (Gemini):** Polish docs/README in parallel.
- Final commit + push, confirm CI still green, tag the milestone.
- Assemble the demo: run report + digest + Discord screenshot.

---

## Contingency: Priority Order (If session gets cut short)
1. **Push + CI green + PR** (Non-negotiable — protects all the work)
2. **Discord bot working** (Highest demo value)
3. **Live geocoding validation**
4. **`Locations` polish + digest** (Nice-to-have)

## Agent Allocation
- **Claude Code (Opus):** CI/bot debugging, the `Locations` fix — the judgment-heavy lanes.
- **Codex (GPT-5-Codex):** Precise patches surfaced during live runs.
- **Antigravity (Gemini):** Docs, README badge, demo artifacts, and any extra test coverage — in parallel.

## Biggest Risks
- **Discord Bot:** Never live-run; timebox it and have the "document the gap" fallback ready.
- **IG Reliability:** Login walls/rate limits may make live runs flaky. Capture the hit rate rather than fighting it.
- **Network:** Without the hotspot, blocks 1, 3, and 4 are completely blocked.
