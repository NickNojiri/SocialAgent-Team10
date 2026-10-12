# ADR-0006: Capture is an explicit fallback chain that records which rung served each URL, with a circuit breaker on the logged-in source

- **Status:** accepted (chain, provenance, breaker) · proposed (replace the logged-in rung with Meta's oEmbed API — Track A's call)
- **Decided:** 2026-10-12 · **Recorded:** 2026-10-12 · **Owner:** Nick
- **Related:** `pipeline/orchestrator.py::_capture`, `docs/handoffs/TRACK-A-COMPLIANT-CAPTURE.md`,
  `docs/THREAT_MODEL.md` T3, ARCHITECTURE §3

## Context

Capture tries three ways to read a reel: the logged-in Instagram source (instagrapi, a
burner account), the public page in Chromium, and the `/embed/` page over plain HTTP. The
order lived inline in `IngestionPipeline.run()`, interleaved with sinks, callbacks and
timeouts, and nothing recorded which way served a URL.

Three things made that untenable:

1. **The account got flagged.** On 2026-10-08 a 200-reel corpus pull through the logged-in
   source got the burner auto-flagged by Instagram (restored automatically the next day).
   The orchestrator's only guard was the source's own "first login failed" flag; a login
   that succeeds and *then* gets challenged was retried on every following URL — the
   pattern that turns a flag into a ban.
2. **We couldn't measure the chain.** Track A's Phase 2 item (#2) asks for the pass rate
   before and after changing the fallback order. Without per-URL provenance that number
   has to be inferred from logs.
3. **The next rung is coming.** The proposed permitted route (Meta's oEmbed API) needs a
   place to slot in that doesn't mean editing a 90-line loop.

## Options considered

1. **Leave it inline, add logging** — cheapest, but every future rung edits `run()`, and
   log lines are not a metric.
2. **A pluggable "source" interface with a registry** — the long-term shape (Track A's #2
   "one fetch interface"), but it is Track A's design to make and touches their modules.
3. **Extract the chain into one orchestrator method, record the rung, add a per-run
   breaker** — *chosen*. All in Platform-owned code; the interface work stays with Track A
   and gets a single, obvious insertion point.

## Decision

`IngestionPipeline._capture(url, browser)` walks the chain — `authed` → `browser` →
`embed` — and returns what it got plus the rung that served it. Every `IngestionResult`
carries `capture_path` (`authed` | `browser` | `embed` | `none`), and the run report
counts them. Chromium is opened lazily (`_LazyBrowser`) and closed once per run.

The logged-in rung has a per-run circuit breaker: after `AUTHED_MAX_MISSES` (default 3)
Instagram URLs in a row come back empty or raise, it is skipped for the rest of the run
and the public path serves the remaining URLs. Non-Instagram URLs never count as misses.
A source that raises now degrades to the next rung instead of failing the URL.

## Consequences

**Good:** a flagged account is touched at most 3 more times per run, not once per URL;
the capture harness and `/dash` can report rung shares directly; adding oEmbed is one
step in `_capture`; a source bug can no longer fail a capture outright.

**Bad:** the breaker is per run — a long-lived worker starts each job fresh, so a flagged
account still sees up to 3 attempts per job. A process-wide breaker with a cool-down is
the follow-up if that shows up in practice. Records lose the location tag and coordinates
when a URL falls back past the logged-in rung (51% of the 2026-10-08 pull had a tag).

**Follow-ups:** Track A decides on the oEmbed rung and the fetch interface; Track D adds
"capture account banned" to the threat model; `/dash` gets a capture-path panel.

## Evidence

- Incident: 200-reel pull on 2026-10-08, flag 2026-10-09, auto-restore the same week
  (`docs/handoffs/TRACK-A-COMPLIANT-CAPTURE.md`).
- `test_ingestion.py::TestCaptureChain` — rung recorded per URL, breaker stops calling a
  dead source after 3 misses, non-IG URLs don't trip it, a raising source falls back, a
  login wall recovers through the embed rung.
