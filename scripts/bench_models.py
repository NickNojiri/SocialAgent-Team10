"""Benchmark STT + LLM models on THIS machine for SpotBot's actual workload.

    python scripts/bench_models.py path\\to\\reel.mp4          # or .mp3/.wav
    python scripts/bench_models.py reel.mp4 --stt tiny base small distil-small.en
    python scripts/bench_models.py --llm-only                  # skip STT

What it measures, per model:
  STT  — transcription wall time, realtime factor, and the transcript itself
         (eyeball the VENUE NAMES: proper nouns are what this app needs right).
  LLM  — venue extraction on a fixed messy caption+transcript through the same
         payload the pipeline uses: wall time + the venue each model found.

Run it twice — the first pass includes model load; the second is the honest
steady-state number (Ollama keeps models warm ~5 min).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.ingestion.config import IngestionSettings

STT_MODELS_DEFAULT = ["tiny", "base", "small"]
LLM_MODELS_DEFAULT = ["llama3.2:1b", "llama3.2:3b", "llama3.1:8b"]

# A deliberately messy, realistic fixture: venue only in the "transcript".
LLM_CAPTION = "no cap this place>>> 😮‍💨🔥 link in bio #foodie #socal #hiddengem"
LLM_TRANSCRIPT = (
    "okay so we found this spot called Menya Hanabi in Artesia, it's a mazesoba "
    "place, get the original with extra garlic, they close at nine"
)
EXPECTED_VENUE = "menya hanabi"


def bench_stt(audio_path: str, models: list[str]) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("!! faster-whisper not installed (pip install faster-whisper) — skipping STT")
        return

    import av  # bundled with faster-whisper; used to get the clip duration

    with av.open(audio_path) as container:
        duration = float(container.duration or 0) / 1_000_000 or 1.0

    print(f"\n=== STT bench: {audio_path} ({duration:.0f}s of audio) ===")
    print(f"{'model':<18}{'load(s)':>9}{'transcribe(s)':>15}{'x realtime':>12}")
    transcripts: dict[str, str] = {}
    for name in models:
        t0 = time.perf_counter()
        model = WhisperModel(name, device="cpu", compute_type="int8")
        load_s = time.perf_counter() - t0
        t1 = time.perf_counter()
        segments, _info = model.transcribe(audio_path, beam_size=1, vad_filter=True)
        text = " ".join(s.text for s in segments).strip()
        run_s = time.perf_counter() - t1
        transcripts[name] = text
        print(f"{name:<18}{load_s:>9.1f}{run_s:>15.1f}{duration / max(run_s, 0.01):>11.1f}x")
        del model

    print("\n--- transcripts (check the venue/proper nouns!) ---")
    for name, text in transcripts.items():
        print(f"\n[{name}] {text[:400]}")


def bench_llm(settings: IngestionSettings, models: list[str]) -> None:
    from src.ingestion.pipeline.llm_extractor import LlmFieldExtractor
    from src.ingestion.pipeline.normalizer import build_llm_payload
    from src.ingestion.schemas.snapshot import RawPostSnapshot

    raw = RawPostSnapshot(
        source_url="https://www.instagram.com/reel/BENCH/",
        platform="instagram",
        caption=LLM_CAPTION,
        transcript=LLM_TRANSCRIPT,
    )
    payload = build_llm_payload(raw)

    print("\n=== LLM bench: venue extraction from a messy caption+transcript ===")
    print(f"expected venue ≈ {EXPECTED_VENUE!r}")
    print(f"{'model':<16}{'time(s)':>9}   venue found")
    for name in models:
        s = settings.model_copy(update={"ollama_model": name})
        extractor = LlmFieldExtractor(s)
        t0 = time.perf_counter()
        try:
            result = extractor.extract(payload)
        except Exception as exc:
            print(f"{name:<16}{'—':>9}   !! {type(exc).__name__}: {exc}")
            continue
        took = time.perf_counter() - t0
        venue = (result.venue_name if result else None) or "(none — heuristics fallback)"
        hit = "✅" if EXPECTED_VENUE in venue.lower() else "❌"
        print(f"{name:<16}{took:>9.1f}   {hit} {venue}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="?", help="an mp4/mp3/wav clip (a downloaded reel works)")
    parser.add_argument("--stt", nargs="*", default=STT_MODELS_DEFAULT)
    parser.add_argument("--llm", nargs="*", default=LLM_MODELS_DEFAULT)
    parser.add_argument("--llm-only", action="store_true")
    args = parser.parse_args()

    settings = IngestionSettings()
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3.0).json()
        installed = {m["name"] for m in tags.get("models", [])}
        print(f"Ollama up — installed: {', '.join(sorted(installed)) or '(none)'}")
        llm_models = [m for m in args.llm if m in installed]
        skipped = [m for m in args.llm if m not in installed]
        if skipped:
            print(f"   (skipping not-pulled: {', '.join(skipped)} — `ollama pull <name>` to include)")
    except Exception:
        print("!! Ollama not reachable — skipping the LLM bench")
        llm_models = []

    if not args.llm_only:
        if args.audio:
            bench_stt(args.audio, args.stt)
        else:
            print("\n(no audio file given — STT bench skipped; pass a reel .mp4 to run it)")

    if llm_models:
        bench_llm(settings, llm_models)

    print(
        "\nHow to read this: pick the smallest STT model that still spells the venue "
        "right, and the smallest LLM with a ✅. Speed you feel every capture; "
        "accuracy you feel forever (a mangled venue poisons the catalog)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
