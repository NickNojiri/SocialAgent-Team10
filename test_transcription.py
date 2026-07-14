"""
Unit test for the voice-message transcription module (app/transcription.py).

Uses a fake Whisper model so it runs fast with no model download —
`python test_transcription.py`.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

import transcription


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeInfo:
    duration = 2.5
    language = "en"
    language_probability = 0.99


class FakeModel:
    def transcribe(self, path, **kwargs):
        assert os.path.exists(path), "temp audio file should exist during transcription"
        return [FakeSegment(" let's grab dinner "), FakeSegment("friday at 7 ")], FakeInfo()


class FakeAttachment:
    def __init__(self, filename="voice-message.ogg", size=1024):
        self.filename = filename
        self.size = size

    async def save(self, path):
        with open(path, "wb") as f:
            f.write(b"\x00" * 64)  # fake audio bytes; FakeModel never decodes them


async def fake_get_model():
    return FakeModel()


def run_transcription_tests():
    print("🚀 Testing app/transcription.py\n")

    # 1. Happy path: segments are joined and stripped
    transcription._get_model = fake_get_model
    transcription._AVAILABLE = True
    text = asyncio.run(transcription.transcribe_attachment(FakeAttachment()))
    assert text == "let's grab dinner friday at 7", f"unexpected transcript: {text!r}"
    print(f"✅ Transcript assembled correctly: {text!r}")

    # 2. Oversized attachments are skipped
    big = FakeAttachment(size=transcription.MAX_AUDIO_BYTES + 1)
    assert asyncio.run(transcription.transcribe_attachment(big)) is None
    print("✅ Oversized attachment rejected")

    # 3. Graceful when faster-whisper isn't installed
    transcription._AVAILABLE = False
    assert not transcription.transcription_available()
    assert asyncio.run(transcription.transcribe_attachment(FakeAttachment())) is None
    transcription._AVAILABLE = True
    print("✅ Degrades gracefully without faster-whisper")

    print("\n🎉 All transcription tests passed")


if __name__ == "__main__":
    run_transcription_tests()
