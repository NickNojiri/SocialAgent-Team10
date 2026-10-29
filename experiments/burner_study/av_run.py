"""Audio + on-screen text for the low-text burner reels (~150), after the 20-reel pilot.

Uses the repo's own Transcriber (with the ffmpeg-decode fix) and ImageTextReader, Whisper
'small'. Burner pacing: at most 15 fetches, then 10 minutes. Stops (no retry) on a failed
login or 3 failed fetches in a row. Resumable: reels already in the output are skipped.
Writes data/experiments/av_run.jsonl; the log has counts only.
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]
os.chdir(WORK)
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(WORK / "scripts"))
try:
    import truststore  # home network intercepts TLS (CDN downloads, model download)
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

import watch_dm_reels as w  # noqa: E402  (loads WORK/.env)
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.ocr import ImageTextReader  # noqa: E402
from src.ingestion.pipeline.transcriber import Transcriber  # noqa: E402
from src.ingestion.sources.ig_authed import AuthedInstagramSource  # noqa: E402

OUT = WORK / "data" / "experiments" / "av_run.jsonl"
PILOT = WORK / "data" / "experiments" / "pilot_av.jsonl"
CAP, PAUSE = 15, 600


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def codes_in(p: Path) -> set:
    if not p.exists():
        return set()
    return {json.loads(l)["code"] for l in p.read_text(encoding="utf-8").split("\n") if l.strip()}


reels = json.loads((WORK / "data" / "dm_firstpass.json").read_text(encoding="utf-8"))
picks = [r for r in reels if r["unsure"] or r["kind"] in ("unnamed", "later")
         or (r["kind"] == "place" and len(r["caption"]) < 80)]
skip = codes_in(OUT) | codes_in(PILOT)
picks = [r for r in picks if r["code"] not in skip]
log(f"av run: {len(picks)} reels to do ({len(skip)} already done)")
if not picks:
    sys.exit(0)

settings = IngestionSettings(transcribe_enabled=True, whisper_model="small")
try:
    cl = w._client()
except Exception as exc:  # noqa: BLE001
    log(f"LOGIN FAILED, stopping: {type(exc).__name__}: {exc}")
    sys.exit(1)
src = AuthedInstagramSource(settings)
src._client = cl
stt, ocr = Transcriber(settings), ImageTextReader(settings)

misses = 0
for n, r in enumerate(picks, 1):
    if n > 1 and (n - 1) % CAP == 0:
        log(f"{n - 1} done, waiting {PAUSE // 60} min")
        time.sleep(PAUSE)
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
log("av run done")
