# Track to-do lists & handoffs

One document per specialist. Each is a standalone handoff: what already exists in your
area, the honest numbers, the traps, your to-do list phase by phase, and the two
handoffs you owe other tracks.

| Track | Specialist | Doc | Points |
|---|---|---|---|
| A | Capture & Data Sources | [TRACK-A-CAPTURE.md](TRACK-A-CAPTURE.md) | 460 |
| B | Accuracy & Evaluation | [TRACK-B-ACCURACY.md](TRACK-B-ACCURACY.md) | 470 |
| C | Experience & User Research | [TRACK-C-EXPERIENCE.md](TRACK-C-EXPERIENCE.md) | 470 |
| D | Cybersecurity | [TRACK-D-CYBERSECURITY.md](TRACK-D-CYBERSECURITY.md) | 420 |
| — | Architecture & Platform (Nick) | [TRACK-N-PLATFORM.md](TRACK-N-PLATFORM.md) | 470 |

33 features · 2,290 points · four phases of ~3 weeks.

**Read first, whatever your track:** [`docs/KICKOFF.md`](../KICKOFF.md) (clone, install, run
the tests), [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) (how the pieces fit),
[`docs/CODEX_SETUP.md`](../CODEX_SETUP.md) (Codex on your CSULB account),
[`AGENTS.md`](../../AGENTS.md) (rules any coding agent must follow here).

## Phase calendar

| Phase | Dates | Theme |
|---|---|---|
| 1 | Sep 15 – Oct 3 | Measure & lock down |
| 2 | Oct 6 – Oct 24 | First real gains |
| 3 | Oct 27 – Nov 14 | Harden for strangers |
| 4 | Nov 17 – Dec 11 | Reach & resilience |

Dates are the feature-list plan — check them against Canvas before you commit to one.

## Rules that apply to every track

- Branch per feature: `track-<a|b|c|d|n>/<short-name>`. PR into `main`, Nick reviews.
- Never commit `.env`, `data/`, `fixtures/labels.batch*.jsonl`, tokens, passwords, or
  session cookies. No secrets in code, tests, or logs.
- `pytest -k "not live" -q` must pass before you open a PR (291 passing as of Sept 17).
- Anything you claim in a status update should be reproducible by one command you can
  paste. "It works" is not a result; "13/13 on this list, here's the command" is.
- Pushing from the campus/home network needs `git -c http.sslVerify=false push` (TLS
  interception). See [`docs/KICKOFF.md`](../KICKOFF.md).
