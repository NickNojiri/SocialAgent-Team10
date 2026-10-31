# Docs index

One line per doc, so you (or an agent) can open the one you need instead of skimming many.
Dated docs under **History** describe how things were, not how they are; skip them unless
the task asks.

## Start here
| Doc | What it's for |
|---|---|
| [KICKOFF.md](KICKOFF.md) | Onboarding: clone, install, first task per track |
| [SETUP_GUIDE.md](SETUP_GUIDE.md) | From `git clone` to a spot card in Discord, with troubleshooting |
| [USER_GUIDE.md](USER_GUIDE.md) | What users see: every command and button |

## Run and operate
| Doc | What it's for |
|---|---|
| [RUNBOOK.md](RUNBOOK.md) | Start the services, env vars, secrets and key rotation, staging, troubleshooting |
| [RUN_AT_WORK.md](RUN_AT_WORK.md) | Running the capture stack on another machine |
| [MODEL_GUIDE.md](MODEL_GUIDE.md) | Which local models to run on which hardware |
| [SPRINT_BOARD.md](SPRINT_BOARD.md) | The Discord sprint board: commands and the Mon/Wed/Fri routine |

## Architecture and decisions
| Doc | What it's for |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Processes, ports, data stores, one capture end to end (current; wins over older docs) |
| [STACK.md](STACK.md) | The full technology stack on one page |
| [PIPELINE.md](PIPELINE.md) | The ingestion pipeline's phases in detail |
| [adr/](adr/README.md) | Why: ADR-0001 (Chroma per guild) … ADR-0007 (IG webhook) |
| [SRS.md](SRS.md) | Requirements and constraints (C-1/C-2: no paid APIs, no post text off the machine) |

## Accuracy and ML (Track B)
| Doc | What it's for |
|---|---|
| [DATA_PLAN.md](DATA_PLAN.md) | The frozen train/dev/test splits and the rules for reading test |
| [BASELINE.md](BASELINE.md) | Every reported number, with its interval and command |
| [LABELING_GUIDE.md](LABELING_GUIDE.md) | How to label `fixtures/labels.jsonl` |
| [EXTRACTION_ACCURACY.md](EXTRACTION_ACCURACY.md) | The label loop: approach and round history |
| [results/](results/) | Dated experiment write-ups (M2 place picker: `2026-10-27-evaluation-report.html`) |
| [../experiments/burner_study/README.md](../experiments/burner_study/README.md) | How to rerun the M2 model |

## Security and privacy (Track D)
| Doc | What it's for |
|---|---|
| [THREAT_MODEL.md](THREAT_MODEL.md) | Threats T1–T*, mitigations, the attack tests |
| [PRIVACY.md](PRIVACY.md) | The privacy policy users see |
| [ACCESSIBILITY.md](ACCESSIBILITY.md) | The accessibility checklist |

## Course and planning
| Doc | What it's for |
|---|---|
| [CAPSTONE_PLAN.md](CAPSTONE_PLAN.md) | Sprint plan, owners, definition of done |
| [tracks/](tracks/README.md) | Each track's to-do list and handoffs, phase by phase |
| [TEAM_TODO.md](TEAM_TODO.md) | The ranked backlog |
| [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md) | Product phases and the "why" |
| [SPRINT_BOARD_PLAN.md](SPRINT_BOARD_PLAN.md) | How the sprint board tool was built |
| [CODEX_SETUP.md](CODEX_SETUP.md), [CODEX_HANDOFF.md](CODEX_HANDOFF.md) | Setting up and prompting Codex |

## History (dated; read only if asked)
[HANDOFF.md](HANDOFF.md) (Sep 9 extraction handoff) · [MILESTONES.md](MILESTONES.md) (July 2026) ·
[PHASE1_EVIDENCE.md](PHASE1_EVIDENCE.md) · [PAIRING.md](PAIRING.md) · [CODEX_TASKS.md](CODEX_TASKS.md) ·
[IG_AUTH_INGESTION_PLAN.md](IG_AUTH_INGESTION_PLAN.md) · [REEL_TRANSCRIBE_TEST_PLAN.md](REEL_TRANSCRIBE_TEST_PLAN.md) ·
[ML_ADVISOR_BRIEF.md](ML_ADVISOR_BRIEF.md) · [ML_REVIEW_QUESTIONS.md](ML_REVIEW_QUESTIONS.md) · [handoffs/](handoffs/)
