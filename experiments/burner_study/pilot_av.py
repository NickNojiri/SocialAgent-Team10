"""Audio/visual pilot: 20 low-text burner reels -> transcript (Whisper) + cover OCR.

Burner pacing as before: 15, wait 10 min, then 5. Stops (no retry) on a failed login or
3 failed fetches in a row. Writes data/experiments/pilot_av.jsonl; logs counts only.
"""
import json
import shutil
import os
import sys
import time
from datetime import datetime
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
DATA = REPO / "data"
os.chdir(WORK)
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(WORK / "scripts"))

try:
    import truststore  # home network intercepts TLS; use the OS trust store for the CDN downloads
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

import watch_dm_reels as w  # noqa: E402  (loads WORK/.env)
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.ocr import ImageTextReader  # noqa: E402
from src.ingestion.pipeline.transcriber import Transcriber  # noqa: E402
from src.ingestion.sources.ig_authed import AuthedInstagramSource  # noqa: E402

OUT = WORK / "data" / "experiments" / "pilot_av.jsonl"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def whisper_via_ffmpeg():
    """faster-whisper 1.2.1 calls av.open(metadata_errors=...), which PyAV 19 removed. Decode
    with ffmpeg ourselves and hand Whisper a 16 kHz mono array instead (no dependency change)."""
    import subprocess

    import numpy as np
    from faster_whisper import WhisperModel

    model = WhisperModel(IngestionSettings().whisper_model, device="cpu", compute_type="int8")

    def run(path: str):
        pcm = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000",
                              "-f", "s16le", "-"], capture_output=True, check=True).stdout
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return None
        segments, _ = model.transcribe(audio, beam_size=1)
        return " ".join(s.text for s in segments)

    return run


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


reels = json.loads((DATA / "dm_firstpass.json").read_text(encoding="utf-8"))
picks = [r for r in reels if (r["unsure"] or r["kind"] == "unnamed") and r["kind"] in ("place", "unnamed")]
picks = sorted(picks, key=lambda r: len(r["caption"]))[:20]          # least text first
if "--selftest" in sys.argv:   # decode + transcribe a generated 3 s tone: proves the audio path, no IG
    import subprocess
    import tempfile
    p = Path(tempfile.gettempdir()) / "pilot_tone.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:a", "aac", str(p)], check=True)
    print("selftest ok, transcript:", repr(whisper_via_ffmpeg()(str(p))))
    sys.exit(0)
if OUT.exists():
    OUT.unlink()   # the first attempt's transcripts all failed; redo the set
log(f"pilot: {len(picks)} reels to do")
if "--wait" in sys.argv:   # the aborted first attempt used 7 fetches; keep the 15-per-10-min pace
    log("waiting 10 min before the first fetch")
    time.sleep(600)

settings = IngestionSettings()
try:
    cl = w._client()
except Exception as exc:  # noqa: BLE001
    log(f"LOGIN FAILED, stopping: {type(exc).__name__}: {exc}")
    sys.exit(1)
src = AuthedInstagramSource(settings)
src._client = cl
stt, ocr = Transcriber(settings, transcribe_fn=whisper_via_ffmpeg()), ImageTextReader(settings)

misses = 0
for n, r in enumerate(picks, 1):
    if n == 16:
        log("15 done, waiting 10 min before the last 5")
        time.sleep(600)
    t0 = time.time()
    snap = src.fetch_url(r["url"])
    if snap is None:
        misses += 1
        log(f"{n}: fetch failed ({misses} in a row)")
        if misses >= 3:
            log("3 failed fetches in a row, stopping (no retry)")
            sys.exit(1)
        continue
    misses = 0
    video = getattr(snap, "video_url", None)
    transcript = stt.transcribe_url(video) if video else None
    screen = ocr.read_url(snap.image_url)
    with OUT.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"code": r["code"], "order": r["order"], "had_video": bool(video),
                            "transcript": transcript, "ocr": screen}, ensure_ascii=False) + "\n")
    log(f"{n}: video={bool(video)} transcript={len(transcript or '')} chars ocr={len(screen or '')} chars "
        f"({time.time() - t0:.0f}s)")
    time.sleep(2)
log("pilot done")
