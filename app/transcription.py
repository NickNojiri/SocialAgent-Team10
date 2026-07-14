"""
Local speech-to-text for Discord voice messages using faster-whisper.

Runs Whisper entirely on-device (CPU by default) — no cloud API, no key —
matching the project's local/free stack. The model weights are downloaded
once on first use and cached under ~/.cache/huggingface.

If faster-whisper isn't installed the bot still runs; voice messages just
get a "transcription not enabled" reply.
"""

import asyncio
import logging
import os
import tempfile

log = logging.getLogger("discord-bot.transcription")

# Model size: tiny | base | small | medium | large-v3
# "base" is a good CPU default (~140MB, a few seconds per voice message).
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")
MAX_AUDIO_BYTES = int(os.getenv("WHISPER_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))

try:
    from faster_whisper import WhisperModel
    _AVAILABLE = True
except ImportError:
    WhisperModel = None
    _AVAILABLE = False

_model = None
_model_lock = asyncio.Lock()


def transcription_available() -> bool:
    return _AVAILABLE


async def _get_model():
    """Load the Whisper model once, off the event loop."""
    global _model
    async with _model_lock:
        if _model is None:
            log.info(
                f"Loading Whisper model {WHISPER_MODEL!r} "
                f"(device={WHISPER_DEVICE}, compute={WHISPER_COMPUTE}) …"
            )
            _model = await asyncio.to_thread(
                WhisperModel,
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE,
            )
            log.info("Whisper model loaded")
    return _model


def _transcribe_file(model, path: str) -> str:
    segments, info = model.transcribe(path, vad_filter=True)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    log.info(
        f"Transcribed {info.duration:.1f}s of audio "
        f"(language={info.language}, p={info.language_probability:.2f})"
    )
    return text


async def transcribe_attachment(attachment) -> str | None:
    """Download a Discord audio attachment and return its transcript.

    Returns None (never raises) if transcription is unavailable or fails,
    or the audio contained no recognizable speech.
    """
    if not _AVAILABLE:
        return None
    if attachment.size > MAX_AUDIO_BYTES:
        log.warning(f"Audio attachment too large ({attachment.size} bytes) — skipping")
        return None

    try:
        model = await _get_model()

        suffix = os.path.splitext(attachment.filename)[1] or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await attachment.save(tmp_path)
            text = await asyncio.to_thread(_transcribe_file, model, tmp_path)
        finally:
            os.unlink(tmp_path)

        return text or None
    except Exception:
        log.exception("Voice transcription failed")
        return None
