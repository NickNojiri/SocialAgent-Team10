"""Reel audio → text (speech-to-text), feeding the spoken venue/location into the
pipeline alongside the caption.

The venue, neighborhood, or event in a reel is often *said out loud* but never
written in the caption. This stage downloads the post's video (the og:video URL
that IG serves even behind the login overlay), transcribes the audio locally with
Whisper, and hands the transcript to the LLM payload as another text source.

Design matches the rest of the pipeline (LLM/geo): best-effort, default-off until
a video URL exists, fully injectable for offline tests, and a failure is a normal
"no transcript" outcome — transcription never breaks a run. The heavy deps
(faster-whisper, ffmpeg) are imported lazily so importing this module never
requires them, and a machine without them simply gets transcript=None.
"""

import logging
import os
import tempfile
from typing import Callable, Optional

import httpx

from src.ingestion.config import IngestionSettings

log = logging.getLogger("ingestion.transcribe")

# A transcriber maps a local audio/video file path to its transcript (or None).
TranscribeFn = Callable[[str], Optional[str]]

_VIDEO_META_KEYS = ("og:video", "og:video:url", "og:video:secure_url")
_DOWNLOAD_TIMEOUT = 30.0
_MAX_BYTES = 50 * 1024 * 1024  # 50 MB cap — reels are short; refuse anything huge


def video_url_from_meta(meta: dict[str, str]) -> Optional[str]:
    """Pull the playable video URL from page meta — present even behind the wall."""
    for key in _VIDEO_META_KEYS:
        url = meta.get(key)
        if url and url.startswith("http"):
            return url
    return None


class Transcriber:
    def __init__(
        self,
        settings: IngestionSettings,
        transcribe_fn: Optional[TranscribeFn] = None,
    ):
        self.settings = settings
        self._transcribe_fn = transcribe_fn  # lazily defaulted so import stays cheap

    def transcribe_url(self, video_url: str) -> Optional[str]:
        """Download the video and return its transcript, or None on any failure."""
        path = self._download(video_url)
        if path is None:
            return None
        try:
            fn = self._transcribe_fn or self._default_whisper()
            if fn is None:
                return None  # Whisper not installed — degrade quietly
            text = fn(path)
            cleaned = (text or "").strip()
            if cleaned:
                log.info(f"[stt] transcribed {len(cleaned)} chars from {video_url[:60]}…")
            return cleaned or None
        except Exception as exc:  # belt-and-suspenders — never break the run
            log.warning(f"[stt] transcription failed ({type(exc).__name__}: {exc})")
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ── internals ────────────────────────────────────────────────────────────

    def _download(self, video_url: str) -> Optional[str]:
        try:
            with httpx.stream("GET", video_url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as resp:
                resp.raise_for_status()
                fd, path = tempfile.mkstemp(suffix=".mp4")
                total = 0
                with os.fdopen(fd, "wb") as f:
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            log.warning(f"[stt] video exceeded {_MAX_BYTES} bytes; skipping")
                            f.close()
                            os.unlink(path)
                            return None
                        f.write(chunk)
            return path
        except Exception as exc:
            log.warning(f"[stt] video download failed ({type(exc).__name__}: {exc})")
            return None

    def _default_whisper(self) -> Optional[TranscribeFn]:
        """Build a faster-whisper transcriber, or None if it isn't installed."""
        try:
            from faster_whisper import WhisperModel  # heavy, optional
        except ImportError:
            log.info("[stt] faster-whisper not installed; skipping transcription")
            return None

        model_size = self.settings.whisper_model
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

        def _run(path: str) -> Optional[str]:
            segments, _info = model.transcribe(path, beam_size=1)
            return " ".join(seg.text for seg in segments)

        return _run
