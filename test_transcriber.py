"""Offline tests for the reel audio→text transcription stage (Phase 2.5).

Network-free: the Whisper backend and video download are both replaced with
fakes, so these run in CI. Proves the wiring — video-URL discovery, transcript
attachment, payload/grounding integration, and the never-crash degrade paths.
"""

import shutil
import subprocess
import sys
import types
from unittest.mock import patch

import pytest

from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.llm_extractor import _payload_text
from src.ingestion.pipeline.normalizer import build_llm_payload
from src.ingestion.pipeline.transcriber import Transcriber, decode_audio, video_url_from_meta
from src.ingestion.schemas.snapshot import RawPostSnapshot

SETTINGS = IngestionSettings(transcribe_enabled=True)
SPOKEN = "What's up everyone we are at Casa Loma birria in Long Beach come through"


class TestVideoUrlFromMeta:
    def test_prefers_og_video(self):
        meta = {"og:video": "https://cdn.ig/a.mp4", "og:video:url": "https://cdn.ig/b.mp4"}
        assert video_url_from_meta(meta) == "https://cdn.ig/a.mp4"

    def test_falls_back_to_secure_url(self):
        meta = {"og:video:secure_url": "https://cdn.ig/s.mp4"}
        assert video_url_from_meta(meta) == "https://cdn.ig/s.mp4"

    def test_none_when_absent(self):
        assert video_url_from_meta({"og:title": "x"}) is None

    def test_ignores_non_http(self):
        assert video_url_from_meta({"og:video": "blob:xyz"}) is None


class TestTranscriber:
    def test_transcribes_with_injected_backend(self):
        # Fake the download (return a fake path) and inject a fake STT backend.
        t = Transcriber(SETTINGS, transcribe_fn=lambda path: SPOKEN)
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), \
             patch("os.unlink"):
            assert t.transcribe_url("https://cdn.ig/v.mp4") == SPOKEN

    def test_download_failure_returns_none(self):
        t = Transcriber(SETTINGS, transcribe_fn=lambda path: SPOKEN)
        with patch.object(Transcriber, "_download", return_value=None):
            assert t.transcribe_url("https://cdn.ig/v.mp4") is None

    def test_backend_exception_degrades_to_none(self):
        def boom(path):
            raise RuntimeError("whisper exploded")

        t = Transcriber(SETTINGS, transcribe_fn=boom)
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), \
             patch("os.unlink"):
            assert t.transcribe_url("https://cdn.ig/v.mp4") is None

    def test_empty_transcript_is_none(self):
        t = Transcriber(SETTINGS, transcribe_fn=lambda path: "   ")
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), \
             patch("os.unlink"):
            assert t.transcribe_url("https://cdn.ig/v.mp4") is None

    def test_missing_whisper_degrades_to_none(self):
        # No injected fn and faster-whisper not available → None, no crash.
        t = Transcriber(SETTINGS, transcribe_fn=None)
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), \
             patch.object(Transcriber, "_default_whisper", return_value=None), \
             patch("os.unlink"):
            assert t.transcribe_url("https://cdn.ig/v.mp4") is None


class TestDefaultWhisperBackend:
    """Regression: faster-whisper 1.2.1 + PyAV >= 15 made every real transcription raise
    inside faster-whisper's own audio loader, which the catch-all turned into a silent
    None. The backend must hand Whisper decoded samples, never the file path."""

    def test_whisper_gets_samples_not_a_path(self, monkeypatch):
        np = pytest.importorskip("numpy")
        seen = {}

        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, audio, **kwargs):
                seen["audio"] = audio
                return [types.SimpleNamespace(text="we are at Casa Loma")], None

        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
        samples = np.zeros(16000, dtype=np.float32)
        with patch("src.ingestion.pipeline.transcriber.decode_audio", return_value=samples):
            run = Transcriber(SETTINGS)._default_whisper()
            assert run("/tmp/fake.mp4") == "we are at Casa Loma"
        assert not isinstance(seen["audio"], str)

    def test_no_audio_is_none_not_a_crash(self, monkeypatch):
        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, audio, **kwargs):
                raise AssertionError("must not be called without audio")

        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
        with patch("src.ingestion.pipeline.transcriber.decode_audio", return_value=None):
            assert Transcriber(SETTINGS)._default_whisper()("/tmp/fake.mp4") is None

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs the ffmpeg binary")
    def test_decode_audio_reads_a_real_file(self, tmp_path):
        pytest.importorskip("numpy")
        clip = tmp_path / "tone.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(clip)], check=True)
        audio = decode_audio(str(clip))
        assert audio is not None and str(audio.dtype) == "float32"
        assert 15000 < audio.size < 17500          # ~1 s at 16 kHz
        assert float(abs(audio).max()) > 0.1       # a real tone, not silence


class TestPayloadIntegration:
    def test_transcript_enters_llm_payload(self):
        raw = RawPostSnapshot(source_url="x", caption="link in bio", transcript=SPOKEN)
        payload = build_llm_payload(raw)
        assert payload["transcript"] == SPOKEN

    def test_transcript_grounds_spoken_venue(self):
        """A venue only spoken (not in caption) must survive the LLM grounding check."""
        raw = RawPostSnapshot(source_url="x", caption="omg go here", transcript=SPOKEN)
        haystack = _payload_text(build_llm_payload(raw))
        assert "Casa Loma" in haystack
        assert "Long Beach" in haystack

    def test_no_transcript_key_when_absent(self):
        raw = RawPostSnapshot(source_url="x", caption="just a caption")
        assert "transcript" not in build_llm_payload(raw)
