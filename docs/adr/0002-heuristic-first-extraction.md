# ADR-0002: Heuristic slot parser first; the local LLM is a gap-filler

- **Status:** accepted
- **Decided:** 2026-09-08 (reversing the June "LLM-first" design) · **Recorded:** 2026-09-15 · **Owner:** Nick (Track B owns the parser and the corpus)
- **Related:** `docs/EXTRACTION_ACCURACY.md`, `docs/ML_REVIEW_QUESTIONS.md`, `docs/HANDOFF.md`, [ADR-0001](0001-chromadb-per-guild-collections.md)

## Context

A spot card is only as good as the venue name on it. The June pipeline handed the
caption to a local Ollama model and trusted the JSON back, with regex heuristics as the
fallback. On 2026-09-08 the live catalog had **12 of 18 spots named "Locations"** — page
chrome scraped off Instagram — and the rest were a mix of containers ("Disneyland" for
a stall inside it), decoy cities and promo posts. Nothing measured how often any of it
was right. The machine has no GPU; a 3B–8B chat model on CPU takes 30–56 s per call.
The two product constraints — $0 marginal cost and post text staying local — rule out
hosted models in the default path.

## Options considered

1. **LLM-first, local (the original design).** Measured on the 15-row seed corpus in
   *override* mode: venue **55%** vs. **73%** for the heuristics alone; it overrode
   "Waterfall Chicken" with the decoy city "Carson". Worse, and slow.
2. **LLM as an always-on gap-filler.** Same corpus, LLM only fills fields the
   heuristics left blank: venue 73% — identical to heuristics — "contributes nothing,
   costs 30–56 s/call."
3. **Hosted frontier model (Claude Haiku, ~$0.002/capture).** Would likely beat both
   local options, but sends caption text off-box and adds a per-capture cost. Kept as a
   *gated experiment*, not a default: run over the labeled corpus, decide from the
   number, and only ever on low-confidence rows (see follow-ups).
4. **Fine-tune a small local model.** ~400 labeled rows and no GPU — cannot be trained
   or evaluated to a difference bigger than the confidence interval.
5. **Deterministic slot-first parser + a labeled corpus + a CI ratchet** — chosen.
   Captions have a regular shape (hook · venue mention · location · dish · time · tags);
   named slots (`@handle` after *from/at/by*, `from X`, quoted names, 📍 lines, area
   hashtags) fill the fields, each with a confidence that says *which slot* filled it.

## Decision

`normalize()` always computes the heuristic baseline. When Ollama is reachable and
`llm_enabled` is true (the default), `LlmFieldExtractor` runs as a **gap-filler**: it may
fill a field the heuristics left blank, its output is discarded unless the venue and
location strings appear verbatim in the input (`_strip_hallucinations`), and it never
overrides a confident heuristic slot or structured JSON-LD data. If Ollama is down, a
2-second pre-flight turns the LLM off for the whole run. Accuracy is measured, not
assumed: every change is scored on `fixtures/labels.jsonl` with a deterministic
train/test split, tuned on `train`, and CI fails a change that lowers the `train`
floors (`test_extraction_labels.py`). User corrections persist as a deterministic alias
table (`fixtures/venue_aliases.json`) that is a product feature — and is *excluded* from
the held-out score (`venue_aliases.train.json`).

## Consequences

**Good:** capture is fast and deterministic on CPU; every accuracy claim has a
reproducible command behind it; the failure modes are inspectable rules, not prompts;
the team caught its own evaluation over-reporting (49.6% → 37.4%) precisely because the
scoring was explicit.

**Bad:** the honest held-out numbers are modest — venue exact **37.4%** (95% CI
29–47), category **63.2%** against a 54.7% majority baseline, promo rejection 50%
precision — and rules plateau; the parser sits at roughly half of the **78%** extractive
ceiling. The test set has absorbed six rounds of tuning and a `--split test` look is
now rationed. The gap-filler still costs 30–56 s on any box where Ollama has a chat
model pulled, for no measured gain.

**Follow-ups:** (1) Track B, Sprint 2: paired before/after test (McNemar) for any new
round, intervals on every number. (2) Decide whether `llm_enabled` should default to
**False** in the admin path once the paired test confirms the gap-filler adds nothing on
the full corpus — a config change, recorded as an amendment here. (3) The Haiku
go/no-go (Sprint 7) runs only on a human-labeled held-out slice; see
`ML_ADVISOR_BRIEF.md`.

## Evidence

- `60fb1357` (2026-09-08) "the label loop — slot-first parser, eval harness, scraper
  fix"; `docs/EXTRACTION_ACCURACY.md` "Measured results" and "LLM … cut from the
  capture path".
- `src/ingestion/pipeline/normalizer.py::normalize` (gap-fill merge, "never overrides a
  confident heuristic slot"); `pipeline/llm_extractor.py::_strip_hallucinations`;
  `pipeline/orchestrator.py::_ollama_reachable`; `cli.py::build_extractor`.
- `d6354107` — transcript experiment: naive transcript feed cost −9 category; kept as a
  venue/location signal only.
- `4b266d2b` (2026-09-10) — test-set leakage found and fixed; numbers in
  `docs/ML_REVIEW_QUESTIONS.md` and `python scripts/eval_diagnostics.py`.
- Constraints: `docs/TEAM_TODO.md` ("Zero-Dollar Stack", "Text stays local"),
  `docs/MODEL_GUIDE.md` §2 ("The premium case, honestly").
