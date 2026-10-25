"""Offline tests for the whole-video summaries: what a reel shows (still frames → a local
vision model) and what a reel that ISN'T a place is about, so the bot can still say
something useful. Ollama, Whisper, ffmpeg and the download are all faked.
"""

import shutil
import subprocess
from unittest.mock import patch

import httpx
import pytest

import src.ingestion.pipeline.orchestrator as orchestrator
from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.orchestrator import IngestionPipeline
from src.ingestion.pipeline.summarizer import describe_frames, summarize_post
from src.ingestion.pipeline.transcriber import Transcriber, extract_frames
from src.ingestion.schemas.snapshot import RawPostSnapshot
from src.ingestion.serving.failures import failures

SETTINGS = IngestionSettings(geocode_enabled=False, video_frames=2)
WEDDING = "the bride's mom stood up at the reception and said she never approved"
PLACE_CAPTION = ("Late-night birria tacos pop-up at Casa Loma 📍 4th St, Long Beach — "
                 "this Friday 8pm! #birria #LongBeachFood #tacos")


class TestTranscribeMedia:
    def test_one_download_gives_transcript_and_frames(self):
        t = Transcriber(SETTINGS, transcribe_fn=lambda p: WEDDING, frames_fn=lambda p: [b"a", b"b", b"c"])
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4") as dl, patch("os.unlink"):
            transcript, frames = t.transcribe_media("https://cdn.ig/v.mp4", frames=2)
        assert transcript == WEDDING and frames == [b"a", b"b"] and dl.call_count == 1

    def test_frames_failing_keeps_the_transcript(self):
        def boom(path):
            raise RuntimeError("no video track")

        t = Transcriber(SETTINGS, transcribe_fn=lambda p: WEDDING, frames_fn=boom)
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), patch("os.unlink"):
            assert t.transcribe_media("https://cdn.ig/v.mp4", frames=2) == (WEDDING, [])

    def test_no_frames_asked_never_reads_them(self):
        def must_not_run(path):
            raise AssertionError("frames read when none were asked for")

        t = Transcriber(SETTINGS, transcribe_fn=lambda p: WEDDING, frames_fn=must_not_run)
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), patch("os.unlink"):
            assert t.transcribe_url("https://cdn.ig/v.mp4") == WEDDING

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs the ffmpeg binary")
    def test_extract_frames_reads_a_real_file(self, tmp_path):
        clip = tmp_path / "bars.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=duration=7:size=320x240:rate=10", str(clip)], check=True)
        frames = extract_frames(str(clip), count=2)
        assert len(frames) == 2 and all(f[:2] == b"\xff\xd8" for f in frames)   # JPEGs
        sheet = extract_frames(str(clip), count=2, tile=True)
        assert len(sheet) == 1 and sheet[0][:2] == b"\xff\xd8"                    # one contact sheet


class _FakeOllama:
    def __init__(self, text="A bride and her family argue at a wedding reception."):
        self.text, self.bodies = text, []

    def __call__(self, url, json=None, timeout=None):
        self.bodies.append(json)
        return httpx.Response(200, json={"response": self.text}, request=httpx.Request("POST", url))


class TestSummaries:
    def test_post_summary_uses_speech_and_visuals(self, monkeypatch):
        fake = _FakeOllama()
        monkeypatch.setattr(httpx, "post", fake)
        text = summarize_post("wedding drama 😭", WEDDING, "a reception hall, a woman holding a mic", SETTINGS)
        assert text == fake.text
        prompt = fake.bodies[0]["prompt"]
        assert WEDDING in prompt and "holding a mic" in prompt
        assert fake.bodies[0]["model"] == SETTINGS.ollama_model

    def test_post_summary_skips_a_bare_caption(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("called the model"))
        assert summarize_post("lol", None, None, SETTINGS) is None

    def test_describe_frames_sends_images_to_the_vision_model(self, monkeypatch):
        fake = _FakeOllama("A reception hall with round tables.")
        monkeypatch.setattr(httpx, "post", fake)
        assert describe_frames([b"jpeg1", b"jpeg2"], SETTINGS) == "A reception hall with round tables."
        body = fake.bodies[0]
        assert body["model"] == SETTINGS.vision_model and len(body["images"]) == 2

    def test_model_down_is_none_not_a_crash(self, monkeypatch):
        def down(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "post", down)
        assert describe_frames([b"x"], SETTINGS) is None
        assert summarize_post("a long enough caption about nothing", WEDDING, None, SETTINGS) is None


class _FakeTranscriber:
    def __init__(self, transcript):
        self.transcript, self.asked = transcript, []

    def transcribe_media(self, video_url, frames=0):
        self.asked.append(frames)
        return self.transcript, [b"f"] * frames

    def transcribe_url(self, video_url):
        return self.transcript


class _Source:
    def __init__(self, caption):
        self.caption = caption

    def fetch_url(self, url):
        return RawPostSnapshot(source_url=url, caption=self.caption, video_url="https://cdn.ig/v.mp4")


async def _run(caption, transcript, monkeypatch, **kwargs):
    monkeypatch.setattr(orchestrator, "_ollama_reachable", lambda settings: True)
    transcriber = _FakeTranscriber(transcript)
    pipeline = IngestionPipeline(SETTINGS, authed_source=_Source(caption), transcriber=transcriber, **kwargs)
    report = await pipeline.run(["https://www.instagram.com/reel/DenJ7IAIX2k/"])
    return report.results[0], transcriber


class TestPipeline:
    @pytest.mark.asyncio
    async def test_a_reel_that_isnt_a_place_still_gets_a_summary(self, monkeypatch):
        seen = {}

        def post_summarizer(caption, transcript, visual):
            seen.update(caption=caption, transcript=transcript, visual=visual)
            return "Wedding drama: the bride's mom objects during the toast."

        result, transcriber = await _run(
            "this wedding got out of hand 😭", WEDDING, monkeypatch,
            post_summarizer=post_summarizer, frame_describer=lambda frames: f"{len(frames)} frames of a reception")
        assert result.record is None
        assert result.post_summary == "Wedding drama: the bride's mom objects during the toast."
        assert seen == {"caption": "this wedding got out of hand 😭", "transcript": WEDDING,
                        "visual": "2 frames of a reception"}
        assert transcriber.asked == [2]
        assert failures([result])[0]["summary"] == result.post_summary

    @pytest.mark.asyncio
    async def test_a_place_card_summary_gets_the_visuals_too(self, monkeypatch):
        calls = []

        def summarizer(caption, transcript, visual=None):
            calls.append(visual)
            return "Birria tacos at Casa Loma."

        result, _ = await _run(PLACE_CAPTION, "come through to Casa Loma", monkeypatch,
                               summarizer=summarizer, frame_describer=lambda frames: "tacos on a grill")
        assert result.record is not None and result.record.summary == "Birria tacos at Casa Loma."
        assert calls == ["tacos on a grill"] and result.post_summary is None

    @pytest.mark.asyncio
    async def test_no_frame_describer_reads_no_frames(self, monkeypatch):
        result, transcriber = await _run("this wedding got out of hand 😭", WEDDING, monkeypatch,
                                         post_summarizer=lambda c, t, v: "summary")
        assert transcriber.asked == [] and result.post_summary == "summary"

    @pytest.mark.asyncio
    async def test_ollama_down_means_no_summary_and_no_frames(self, monkeypatch):
        transcriber = _FakeTranscriber(WEDDING)
        monkeypatch.setattr(orchestrator, "_ollama_reachable", lambda settings: False)
        pipeline = IngestionPipeline(SETTINGS, authed_source=_Source("this wedding got out of hand"),
                                     transcriber=transcriber,
                                     post_summarizer=lambda c, t, v: pytest.fail("summarized"),
                                     frame_describer=lambda f: pytest.fail("described"))
        result = (await pipeline.run(["https://www.instagram.com/reel/DenJ7IAIX2k/"])).results[0]
        assert result.post_summary is None and transcriber.asked == []

    def test_failures_leave_summary_out_when_there_is_none(self):
        from src.ingestion.schemas.results import FetchStatus, IngestionResult

        result = IngestionResult(url="u", fetch_status=FetchStatus.OK, rejection_reason="post is not clearly about x")
        assert failures([result]) == [{"url": "u", "class": "not_a_place"}]
