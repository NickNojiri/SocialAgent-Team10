"""Tunable knobs for the ingestion engine. Defaults are deliberately polite."""

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


def _default_chromium_executable_path() -> Optional[str]:
    """Allow pointing Playwright at a pre-installed Chromium.

    Some environments ship a Chromium build that doesn't match the version
    Playwright would otherwise download (e.g. CI sandboxes, TLS-intercepting
    networks where the auto-download is blocked). Setting
    PLAYWRIGHT_EXECUTABLE_PATH (or CHROMIUM_EXECUTABLE_PATH) lets the launcher
    use that binary instead of failing with "executable doesn't exist".

    As a last resort, if PLAYWRIGHT_BROWSERS_PATH points at a managed browser
    pool that exposes a ready-to-use `chromium` symlink, use that. This keeps
    pre-provisioned sandboxes working without anyone exporting an env var,
    while normal dev/CI (no such symlink) falls through to the bundled build.
    """
    explicit = os.getenv("PLAYWRIGHT_EXECUTABLE_PATH") or os.getenv(
        "CHROMIUM_EXECUTABLE_PATH"
    )
    if explicit:
        return explicit

    pool = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if pool:
        candidate = Path(pool) / "chromium"
        if candidate.exists():
            return str(candidate.resolve())

    return None


class IngestionSettings(BaseModel):
    headless: bool = True
    # Optional explicit Chromium binary; falls back to Playwright's bundled
    # build when unset. Read from env so tests/CI need no code changes.
    chromium_executable_path: Optional[str] = Field(
        default_factory=_default_chromium_executable_path
    )
    nav_timeout_ms: int = Field(20_000, gt=0)
    # Cap on waiting for dynamic layouts to finish rendering — never unbounded.
    settle_timeout_ms: int = Field(8_000, gt=0)
    # Minimum spacing between requests to the same domain, same politeness
    # convention as the 1s Nominatim sleep in src/services/location_service.py.
    per_domain_delay_s: float = Field(5.0, ge=0)
    viewport_width: int = 1280
    viewport_height: int = 800
    locale: str = "en-US"
    # Identifying UA, matching the repo's existing convention.
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "SocialAgent-Team10-StudentProject/0.1 (ingestion-engine)"
    )
    # file:// navigation is for offline fixtures/tests only; real runs are http(s).
    allow_file_urls: bool = False
    raw_dir: Path = Path("data/raw")

    # ── Authenticated IG source (roadmap Phase 0.1) ──────────────────────────
    # Credentials for src/ingestion/sources/ig_authed.py — a dedicated burner
    # account only, never a personal one. The session cookie is persisted at
    # ig_session_path (gitignored) so login happens once, not per run.
    ig_username: str = Field(default_factory=lambda: os.getenv("IG_USERNAME", ""))
    ig_password: str = Field(default_factory=lambda: os.getenv("IG_PASSWORD", ""))
    ig_session_path: Path = Path("data/ig_session.json")

    # ── LLM extraction (Phase 2) ─────────────────────────────────────────────
    # When enabled, a local Ollama model isolates the high-fidelity fields and
    # the Phase 1 regex heuristics become the fallback. When Ollama is
    # unreachable the pipeline degrades to heuristics-only — never hard-fails.
    llm_enabled: bool = True
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    llm_timeout_s: float = Field(90.0, gt=0)
    llm_max_retries: int = Field(1, ge=0)  # corrective re-prompts after a parse failure
    llm_seed: int = 42                      # fixed seed + temperature 0 → deterministic
    # Ollama's `format` param. False → "json" (valid-JSON mode, fast). True →
    # the full JSON schema (grammar-constrained decoding: exact, but 10-50x
    # slower on CPU — it times out on small local models). Either way the parse
    # ladder + pydantic still enforce LlmExtraction, so False loses no safety.
    llm_strict_schema: bool = False

    # ── Audio transcription (Phase 2.5) ──────────────────────────────────────
    # Reels often *say* the venue/location out loud without writing it in the
    # caption. When enabled and a video URL is present (og:video survives the IG
    # login overlay), the audio is transcribed locally with Whisper and fed to
    # the LLM alongside the caption. Best-effort: no video, no Whisper, or a
    # download/transcribe failure all degrade to "no transcript" — never a crash.
    transcribe_enabled: bool = False        # opt-in; needs faster-whisper + ffmpeg installed
    whisper_model: str = "base"             # tiny | base | small — accuracy vs speed on CPU

    # ── On-screen text OCR (Phase 2.6) ───────────────────────────────────────
    # Some reels carry their info as burned-in text in the video, never spoken
    # and never in the caption. Reading the *cover image* (og:image) catches
    # most of these cheaply; full video-frame OCR is a noted future upgrade
    # (much slower). Opt-in; needs rapidocr-onnxruntime. Degrades to None.
    ocr_enabled: bool = False

    # ── Fail-fast guardrails ─────────────────────────────────────────────────
    # Hard wall-clock budget per URL for the post-fetch stages (transcribe +
    # LLM + resolve). A hung backend fails the one URL fast with a clear
    # reason instead of riding the caller's HTTP timeout.
    capture_budget_s: float = Field(180.0, gt=0)

    # ── Geographic enrichment (Phase 3) ──────────────────────────────────────
    # Resolves GeoContext location clues to coordinates via the repo's existing
    # Nominatim helper. Degrades to "unresolved" (never crashes) when the geocoder
    # finds nothing or the network is unavailable.
    geocode_enabled: bool = True
    max_geocode_queries: int = Field(3, ge=1)  # cap on specific-tier Nominatim calls per record

    # ── Temporal parsing (Phase 4) ───────────────────────────────────────────
    # Resolves candidate_times → a validated UTC schedule. Relative expressions
    # anchor to the post's fetched_at; expiry is judged against execution time.
    temporal_enabled: bool = True
    assumed_tz: str = "America/Los_Angeles"     # tz for naive parsed times before UTC conversion
    default_duration_hours: float = Field(2.0, gt=0)  # end = start + this (matches the Discord bot)
    reject_expired: bool = True
    expiry_grace_minutes: int = Field(60, ge=0)  # slack before a just-passed start counts as expired

    # ── Vector storage (Phase 5) ─────────────────────────────────────────────
    # Opt-in ChromaDB sink (alongside JSONL, which stays the source of truth).
    # Shares the persistent store with the team's PreferenceStore; isolation is
    # by a dedicated collection. Embeddings are local (Ollama), so the live path
    # works on the current network.
    chroma_enabled: bool = False
    chroma_path: str = "data"                       # same persistent store as PreferenceStore
    chroma_collection: str = "event_inspirations"   # dedicated, isolated collection
    embed_model: str = "mxbai-embed-large"            # 1024-dim embedding model; pull with: ollama pull mxbai-embed-large

    # ── Recommendation serving (Phase 6) ─────────────────────────────────────
    # Spam controls for chat-context recommendations queried from the bot.
    rec_max_distance: float = Field(0.9, ge=0)   # Chroma cosine distance ceiling (lower = closer)
    rec_cooldown_s: float = Field(120.0, ge=0)   # min seconds between auto-suggestions per channel
    rec_max_results: int = Field(3, ge=1)        # max recommendations per response
    rec_dedup_window_s: float = Field(3600.0, ge=0)  # don't repeat an event in a channel within this
