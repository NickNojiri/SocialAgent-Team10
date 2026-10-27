"""Offline tests for the whole-video summaries: what a reel shows (still frames → a local
vision model) and what a reel that ISN'T a place is about, so the bot can still say
something useful. Ollama, Whisper, ffmpeg and the download are all faked.
"""

import json
import shutil
import subprocess
from unittest.mock import patch

import httpx
import pytest

import src.ingestion.pipeline.orchestrator as orchestrator
from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.orchestrator import IngestionPipeline
from src.ingestion.pipeline.summarizer import (clean_title, describe_frames, describe_post, placeholder_gist,
                                               plain_title)
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

    def test_frames_are_described_while_the_speech_is_transcribed(self):
        import threading

        order, done = [], threading.Event()

        def slow_whisper(path):
            assert done.wait(5), "the frames weren't being described alongside"
            order.append("transcript")
            return WEDDING

        def describe(stills):
            order.append(f"described {len(stills)}")
            done.set()

        t = Transcriber(SETTINGS, transcribe_fn=slow_whisper, frames_fn=lambda p: [b"sheet"])
        with patch.object(Transcriber, "_download", return_value="/tmp/fake.mp4"), patch("os.unlink"):
            assert t.transcribe_media("https://cdn.ig/v.mp4", frames=4, on_frames=describe) == (WEDDING, [b"sheet"])
        assert order == ["described 1", "transcript"]

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
        # Spread over the whole clip: a 40 s video still gives 4 frames, not 4 from the start.
        long_clip = tmp_path / "long.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=duration=40:size=160x120:rate=5", str(long_clip)], check=True)
        assert len(extract_frames(str(long_clip), count=4)) == 4
        sheet = extract_frames(str(clip), count=2, tile=True)
        assert len(sheet) == 1 and sheet[0][:2] == b"\xff\xd8"                    # one contact sheet


class _FakeOllama:
    def __init__(self, text="A bride and her family argue at a wedding reception."):
        self.text, self.bodies = text, []

    def __call__(self, url, json=None, timeout=None):
        self.bodies.append(json)
        return httpx.Response(200, json={"response": self.text}, request=httpx.Request("POST", url))


GIST_JSON = json.dumps({"title": "I Married the Girl Who Broke My Heart", "kind": "story",
                        "summary": "He tells how she left him and how they found their way back."})


class TestSummaries:
    def test_describe_post_uses_speech_and_visuals(self, monkeypatch):
        fake = _FakeOllama(GIST_JSON)
        monkeypatch.setattr(httpx, "post", fake)
        gist = describe_post("wedding drama 😭", WEDDING, "a reception hall, a woman holding a mic", "darianthao", SETTINGS)
        assert gist == {"title": "I Married the Girl Who Broke My Heart", "kind": "story",
                        "summary": "He tells how she left him and how they found their way back."}
        body = fake.bodies[0]
        assert WEDDING in body["prompt"] and "holding a mic" in body["prompt"]
        assert body["model"] == SETTINGS.ollama_model and body["format"] == "json"

    def test_an_unknown_kind_becomes_story(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", _FakeOllama(json.dumps({"title": "Cats Doing Cat Things", "kind": "vlog",
                                                                   "summary": "Cats."})))
        assert describe_post("my cats being weird lol", WEDDING, None, "x", SETTINGS)["kind"] == "story"

    def test_not_json_still_gives_a_summary_under_a_plain_title(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", _FakeOllama("A wedding toast goes wrong."))
        gist = describe_post("wedding drama 😭", WEDDING, None, "darianthao", SETTINGS)
        assert gist == {"title": "Wedding drama", "kind": "story", "summary": "A wedding toast goes wrong."}

    def test_every_call_asks_for_the_extractors_context_size(self, monkeypatch):
        # A different num_ctx makes Ollama reload the model on every call (see _generate).
        fake = _FakeOllama(GIST_JSON)
        monkeypatch.setattr(httpx, "post", fake)
        describe_post("wedding drama 😭", WEDDING, None, None, SETTINGS)
        describe_frames([b"jpeg"], SETTINGS)
        assert [b["options"]["num_ctx"] for b in fake.bodies] == [SETTINGS.llm_num_ctx] * 2

    def test_describe_post_skips_a_bare_caption(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("called the model"))
        assert describe_post("lol", None, None, "x", SETTINGS) is None


class TestTitleRules:
    @pytest.mark.parametrize("title", [
        "Wedding",                                    # under 2 words
        "one two three four five six seven eight nine ten eleven",   # over 10
        "Best #wedding ever", "Shoutout to @mom", "Watch at www.site.com", "Wedding day 💍",
        "THIS IS THE CRAZIEST WEDDING", "This video shows a wedding", "Untitled reel",
        "this wedding got out of hand",              # the caption's first line again
    ])
    def test_weird_titles_fall_back_to_the_caption_never_the_account(self, title):
        assert clean_title(title, "this wedding got out of hand\nmore text", "darianthao") == \
            "This wedding got out of hand"

    @pytest.mark.parametrize("caption, transcript, title", [
        ("@svet.lanapak Gryffin live in LA tonight!! 🎶 #edm", None, "Gryffin live in LA tonight"),
        ("🔥🔥 #fyp @someone", "so today we're making the easiest pasta ever. it's great", "So today we're making the easiest"),
        ("#fyp", None, "A reel"),
        (None, None, "A reel"),
    ])
    def test_plain_title_never_tries_hard_or_names_the_account(self, caption, transcript, title):
        assert plain_title(caption, transcript) == title

    def test_placeholder_gist_has_no_handles_tags_or_emoji(self):
        gist = placeholder_gist("@svet.lanapak Gryffin live in LA 🎶 #edm " + "so good " * 60, None)
        assert gist["title"] == "Gryffin live in LA so good" and gist["kind"] == "story"
        assert "@" not in gist["summary"] and "#" not in gist["summary"] and "🎶" not in gist["summary"]
        assert len(gist["summary"]) <= 241 and gist["summary"].endswith("…")

    def test_a_good_title_is_kept_and_tidied(self):
        assert clean_title(' "I Married the Girl Who Broke My Heart." ', "caption", "x") == \
            "I Married the Girl Who Broke My Heart"

    def test_no_account_name_reads_a_reel(self):
        assert clean_title("", None, None) == "A reel"

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
        assert describe_post("a long enough caption about nothing", WEDDING, None, "x", SETTINGS) is None


class _FakeTranscriber:
    def __init__(self, transcript):
        self.transcript, self.asked = transcript, []

    def transcribe_media(self, video_url, frames=0, on_frames=None):
        self.asked.append(frames)
        return self.transcript, [b"f"] * frames      # never calls on_frames: the pipeline must cope

    def transcribe_url(self, video_url):
        return self.transcript


class _Source:
    def __init__(self, caption):
        self.caption = caption

    def fetch_url(self, url):
        return RawPostSnapshot(source_url=url, caption=self.caption, video_url="https://cdn.ig/v.mp4",
                               author_handle="darianthao", image_url="https://cdn.ig/t.jpg")


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

        def post_summarizer(caption, transcript, visual, author):
            seen.update(caption=caption, transcript=transcript, visual=visual, author=author)
            return {"title": "Wedding Toast Gone Wrong", "kind": "story",
                    "summary": "The bride's mom objects during the toast."}

        result, transcriber = await _run(
            "this wedding got out of hand 😭", WEDDING, monkeypatch,
            post_summarizer=post_summarizer, frame_describer=lambda frames: f"{len(frames)} frames of a reception")
        assert result.record is None
        assert result.gist.model_dump() == {"title": "Wedding Toast Gone Wrong", "kind": "story",
                                            "summary": "The bride's mom objects during the toast.",
                                            "author": "darianthao", "image_url": "https://cdn.ig/t.jpg"}
        assert seen == {"caption": "this wedding got out of hand 😭", "transcript": WEDDING,
                        "visual": "2 frames of a reception", "author": "darianthao"}
        assert transcriber.asked == [2]
        assert failures([result])[0]["gist"]["title"] == "Wedding Toast Gone Wrong"

    @pytest.mark.asyncio
    async def test_a_place_card_summary_gets_the_visuals_too(self, monkeypatch):
        calls = []

        def summarizer(caption, transcript, visual=None):
            calls.append(visual)
            return "Birria tacos at Casa Loma."

        result, _ = await _run(PLACE_CAPTION, "come through to Casa Loma", monkeypatch,
                               summarizer=summarizer, frame_describer=lambda frames: "tacos on a grill")
        assert result.record is not None and result.record.summary == "Birria tacos at Casa Loma."
        assert calls == ["tacos on a grill"] and result.gist is None

    @pytest.mark.asyncio
    async def test_no_frame_describer_reads_no_frames(self, monkeypatch):
        result, transcriber = await _run(
            "this wedding got out of hand 😭", WEDDING, monkeypatch,
            post_summarizer=lambda c, t, v, a: {"title": "A Title Here", "kind": "story", "summary": "summary"})
        assert transcriber.asked == [] and result.gist.summary == "summary"

    @pytest.mark.asyncio
    async def test_ollama_down_means_a_plain_placeholder_and_no_frames(self, monkeypatch):
        transcriber = _FakeTranscriber(WEDDING)
        monkeypatch.setattr(orchestrator, "_ollama_reachable", lambda settings: False)
        pipeline = IngestionPipeline(SETTINGS, authed_source=_Source("this wedding got out of hand"),
                                     transcriber=transcriber,
                                     post_summarizer=lambda c, t, v, a: pytest.fail("summarized"),
                                     frame_describer=lambda f: pytest.fail("described"))
        result = (await pipeline.run(["https://www.instagram.com/reel/DenJ7IAIX2k/"])).results[0]
        assert transcriber.asked == []
        assert result.gist.title == "This wedding got out of hand" and result.gist.kind == "story"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("summarizer", [lambda c, t, v, a: None,
                                            lambda c, t, v, a: (_ for _ in ()).throw(RuntimeError("500"))])
    async def test_a_failed_summary_still_gives_a_card_not_couldnt_tell(self, monkeypatch, summarizer):
        # 2026-10-27: Ollama 500'd under VRAM pressure and the bot said "I read it, but couldn't tell".
        result, _ = await _run("this wedding got out of hand 😭 #fyp", WEDDING, monkeypatch,
                               post_summarizer=summarizer)
        assert result.record is None
        assert result.gist.title == "This wedding got out of hand"
        assert "@" not in result.gist.title + result.gist.summary and "#" not in result.gist.summary
        assert failures([result])[0]["gist"]["title"] == "This wedding got out of hand"

    def test_failures_leave_summary_out_when_there_is_none(self):
        from src.ingestion.schemas.results import FetchStatus, IngestionResult

        result = IngestionResult(url="u", fetch_status=FetchStatus.OK, rejection_reason="post is not clearly about x")
        assert failures([result]) == [{"url": "u", "class": "not_a_place"}]
