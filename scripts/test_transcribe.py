"""Transcription + AI-summary demo (YouTube-Gemini-style) for an IG reel.

    python scripts/test_transcribe.py [url ...]

Fetches the reel, pulls its og:video, transcribes the audio locally with
faster-whisper, then summarizes caption + transcript via the local Ollama model.
truststore routes TLS through the OS trust store so the one-time HuggingFace
model download and the CDN video download work on TLS-intercepting networks.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import truststore

truststore.inject_into_ssl()

import httpx

from src.ingestion.config import IngestionSettings
from src.ingestion.browser.session_manager import SocialSessionManager
from src.ingestion.extractors.instagram import InstagramExtractor
from src.ingestion.pipeline.transcriber import Transcriber, video_url_from_html, video_url_from_meta

DEFAULT_URLS = [
    "https://www.instagram.com/reel/DZIPn-ppdU4/",
    "https://www.instagram.com/reel/DZXT8n7p8ME/",
    "https://www.instagram.com/p/DZtDNAokmUh/",
    "https://www.instagram.com/reel/DZpsu1eowNm/",
    "https://www.instagram.com/reel/DYSYFuXsgbH/",
    "https://www.instagram.com/reel/DC4YSNHP10U/",
    "https://www.instagram.com/reel/C907WpjPUp2/",
    "https://www.instagram.com/reel/DY0LbwRyhto/",
    "https://www.instagram.com/reel/DUfLG_mAUc5/",
    "https://www.instagram.com/reel/DRbgCEMkRSs/",
    "https://www.instagram.com/reel/DKAXPHFSmEr/",
    "https://www.instagram.com/reel/C51pgTAycsF/",
    "https://www.instagram.com/reel/DVIRTIYjvt6/",
]


def summarize(caption: str, transcript: str, settings: IngestionSettings) -> str:
    prompt = (
        "Summarize this Instagram reel for someone deciding whether to visit, like a "
        "quick AI video summary. 2-3 sentences covering: the place/venue name, what they "
        "serve or the vibe, the location if mentioned, and one standout detail.\n\n"
        f"Caption:\n{caption or '(none)'}\n\nSpoken audio transcript:\n{transcript or '(none)'}\n"
    )
    resp = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
        timeout=180.0,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


async def main(urls):
    settings = IngestionSettings(whisper_model="base", per_domain_delay_s=2.0)
    extractor = InstagramExtractor()

    print(f"loading faster-whisper '{settings.whisper_model}' (downloads once on first run)…")
    from faster_whisper import WhisperModel

    model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")

    def transcribe_fn(path: str):
        segments, info = model.transcribe(path, beam_size=1, vad_filter=True)
        text = " ".join(s.text for s in segments).strip()
        transcribe_fn.last_lang = info.language
        return text

    transcribe_fn.last_lang = None
    transcriber = Transcriber(settings, transcribe_fn=transcribe_fn)

    async with SocialSessionManager(settings) as session:
        for url in urls:
            print("\n" + "=" * 72)
            print("URL:", url)
            snap = await session.fetch(url)
            print("fetch:", snap.status.value)
            if snap.status.value != "ok":
                print("  page not readable (login wall / error) — can't transcribe")
                continue
            raw = extractor.extract(snap)
            caption = (raw.caption or "").strip()
            print("caption:", caption[:160].replace(chr(10), " ") or "(none)")
            vurl = video_url_from_meta(snap.meta) or video_url_from_html(snap.html)
            if not vurl:
                print("  no video URL found on this post — nothing to transcribe")
                continue
            print("transcribing audio…")
            transcript = transcriber.transcribe_url(vurl)
            print(f"detected language: {transcribe_fn.last_lang}")
            print("--- TRANSCRIPT ---")
            print((transcript or "(empty)")[:350])
            print("\n--- SUMMARY (AI: caption + audio) ---")
            try:
                print(summarize(caption, transcript, settings))
            except Exception as exc:
                print(f"(summary failed: {type(exc).__name__}: {exc})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_URLS))
