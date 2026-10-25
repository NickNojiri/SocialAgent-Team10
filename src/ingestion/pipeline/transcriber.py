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

import json
import logging
import os
import re
import shutil
import subprocess
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
_SAMPLE_RATE = 16000           # what Whisper expects
_DECODE_TIMEOUT = 120.0


def decode_audio(path: str):
    """The video's audio as 16 kHz mono float32 samples (a numpy array), or None.

    Decoded with the ffmpeg binary rather than faster-whisper's own loader:
    faster-whisper 1.2.1 calls `av.open(..., metadata_errors=...)`, which PyAV
    removed, so on a current PyAV every transcription raised a TypeError that the
    catch-all below turned into a silent "no transcript". Handing Whisper the
    samples directly skips PyAV entirely.
    """
    import numpy as np  # comes with faster-whisper; lazy like the other heavy deps

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        log.warning("[stt] ffmpeg not found on PATH; skipping transcription")
        return None
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "error", "-i", path,
         "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=_DECODE_TIMEOUT,
    )
    if proc.returncode != 0 or not proc.stdout:
        log.warning(f"[stt] ffmpeg could not decode audio (exit {proc.returncode})")
        return None
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


def extract_frames(path: str, count: int = 4, every_s: float = 3.0, tile: bool = False) -> list[bytes]:
    """Up to `count` JPEG frames, one every `every_s` seconds from the start, 512 px wide.

    What the video *shows* (a setting, a dish, a sign) for the visual summary; empty
    when ffmpeg is missing or the file has no video track. tile=True returns ONE image,
    the frames as a 2-column contact sheet: a local vision model reads it for the cost of
    a single image (~25 s on an 8 GB GPU, where 4 separate frames took 60-80 s)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or count <= 0:
        return []
    vf = f"fps=1/{every_s:g},scale=512:-2"
    if tile and count > 1:
        vf = f"fps=1/{every_s:g},scale=384:-2,tile=2x{(count + 1) // 2}"
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-i", path,
             "-vf", vf, "-frames:v", "1" if tile and count > 1 else str(count),
             os.path.join(tmp, "frame_%02d.jpg")],
            capture_output=True, timeout=_DECODE_TIMEOUT,
        )
        if proc.returncode != 0:
            log.warning(f"[frames] ffmpeg could not read frames (exit {proc.returncode})")
        names = sorted(n for n in os.listdir(tmp) if n.endswith(".jpg"))
        frames = []
        for name in names:
            with open(os.path.join(tmp, name), "rb") as f:
                frames.append(f.read())
        return frames


def video_url_from_meta(meta: dict[str, str]) -> Optional[str]:
    """Pull the playable video URL from page meta — present even behind the wall."""
    for key in _VIDEO_META_KEYS:
        url = meta.get(key)
        if url and url.startswith("http"):
            return url
    return None


_VIDEO_VERSIONS_RE = re.compile(r'"video_versions":\s*\[\s*\{[^}]*?"url":"([^"]+)"')


def video_url_from_html(html: Optional[str]) -> Optional[str]:
    """Fallback when og:video is absent (IG stopped emitting it for logged-out
    reels): the page still embeds the mp4 in a "video_versions" JSON blob. Pull
    the first URL and JSON-unescape it (\\/ -> /, \\uXXXX -> char)."""
    if not html:
        return None
    match = _VIDEO_VERSIONS_RE.search(html)
    if not match:
        return None
    raw = match.group(1)
    try:
        url = json.loads(f'"{raw}"')
    except Exception:
        url = raw.replace("\\/", "/")
    return url if url.startswith("http") else None


class Transcriber:
    def __init__(
        self,
        settings: IngestionSettings,
        transcribe_fn: Optional[TranscribeFn] = None,
        frames_fn: Optional[Callable[[str], list[bytes]]] = None,
    ):
        self.settings = settings
        self._transcribe_fn = transcribe_fn  # lazily defaulted so import stays cheap
        self._frames_fn = frames_fn          # injectable for tests; default extract_frames

    def transcribe_url(self, video_url: str) -> Optional[str]:
        """Download the video and return its transcript, or None on any failure."""
        return self.transcribe_media(video_url, frames=0)[0]

    def transcribe_media(self, video_url: str, frames: int = 0) -> tuple[Optional[str], list[bytes]]:
        """One download, two reads: the transcript (or None) and `frames` still frames, tiled
        into one contact-sheet image (for the visual summary). Either can fail without the
        other; nothing raises."""
        path = self._download(video_url)
        if path is None:
            return None, []
        try:
            stills: list[bytes] = []
            if frames > 0:
                try:
                    stills = (self._frames_fn or (lambda p: extract_frames(p, count=frames, tile=True)))(path)[:frames]
                except Exception as exc:
                    log.warning(f"[frames] failed ({type(exc).__name__}: {exc})")
            return self._transcribe_path(path, video_url), stills
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ── internals ────────────────────────────────────────────────────────────

    def _transcribe_path(self, path: str, video_url: str) -> Optional[str]:
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
            audio = decode_audio(path)
            if audio is None or audio.size == 0:
                return None              # no audio track, or ffmpeg missing
            segments, _info = model.transcribe(audio, beam_size=1)
            return " ".join(seg.text for seg in segments)

        return _run
