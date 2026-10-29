"""Background job: what's SAID in the reels that have no transcript yet, read from the public page
(no login: the burner is suspended). Corpus + burner reels, dev first, then train; test reels and
reels by a test-split creator are skipped.

Pacing: one reel every 3 minutes, at most 300 a day. Stops for good on a login wall, a 429, a
"please wait"/challenge, or 3 failed fetches in a row. Resumable: reels already in the output are
skipped. Whisper "base" on the CPU, audio decoded with ffmpeg (same as the served app).
Writes data/experiments/speech_backfill.jsonl {code, split, had_video, has_audio, transcript};
logs counts only to data/experiments/queue/speech_backfill.log.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.orchestrator import IngestionPipeline  # noqa: E402
from src.ingestion.pipeline.transcriber import Transcriber, decode_audio  # noqa: E402
from src.ingestion.schemas.results import FetchStatus  # noqa: E402

EXP = REPO / "data" / "experiments"
OUT = EXP / "speech_backfill.jsonl"
LOG = EXP / "queue" / "speech_backfill.log"
GAP_S, PER_DAY, MAX_FAILS = 180, 300, 3
STOP_WORDS = ("429", "challenge", "please wait", "login_required", "rate limit")


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()] if path.exists() else []


def todo():
    """Reels with no transcript anywhere yet: dev first, then train."""
    have = {r["code"] for f in ("av_run.jsonl", "dev_av.jsonl", "pilot_av.jsonl", "speech_backfill.jsonl")
            for r in jsonl(EXP / f) if (r.get("transcript") or "").strip() or f == "speech_backfill.jsonl"}
    decided = {p.stem for p in (EXP / "review_decisions").glob("*.json")}
    rows = [(r, True) for r in ev.load_labels()]
    rows += [(r, ev._code(r["url"]) in decided) for r in jsonl(REPO / "fixtures" / "labels.inbox.jsonl")]
    picked, seen = {"dev": [], "train": []}, set()
    for row, scored in rows:
        code = ev._code(row["url"])
        if code in seen or code in have or ((row.get("input") or {}).get("transcript") or "").strip():
            continue
        seen.add(code)
        split = ev.split_of(row)            # the creator's split; "test" is never touched
        if split == "test":
            continue
        picked["dev" if scored and split == "dev" else "train"].append((code, row["url"]))
    return [(c, u, "dev") for c, u in picked["dev"]] + [(c, u, "train") for c, u in picked["train"]]


class Listen:
    """Stands in for the pipeline's transcriber: keeps the transcript and what the video had."""

    def __init__(self):
        from faster_whisper import WhisperModel

        self.model = WhisperModel("base", device="cpu", compute_type="int8")
        self.dl = Transcriber(IngestionSettings())
        self.last = None

    def transcribe_url(self, video_url):
        self.last = {"had_video": False, "has_audio": False, "transcript": None}
        path = self.dl._download(video_url)
        if not path:
            return None
        self.last["had_video"] = True
        try:
            audio = decode_audio(path)
            if audio is None or audio.size == 0:
                return None
            self.last["has_audio"] = True
            segs, _ = self.model.transcribe(audio, beam_size=1, vad_filter=True)
            self.last["transcript"] = " ".join(s.text for s in segs).strip() or None
            return self.last["transcript"]
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def main():
    queue = todo()
    log(f"start: {len(queue)} reels without speech ({sum(s == 'dev' for *_, s in queue)} dev)")
    settings = IngestionSettings(llm_enabled=False, geocode_enabled=False, temporal_enabled=False,
                                 transcribe_enabled=True, ig_username="", ig_password="")
    listen, fails, day, done_today = Listen(), 0, datetime.now().date(), 0
    for i, (code, url, split) in enumerate(queue):
        if datetime.now().date() != day:
            day, done_today = datetime.now().date(), 0
        if done_today >= PER_DAY:
            log(f"daily cap ({PER_DAY}) reached; resuming tomorrow")
            while datetime.now().date() == day:
                time.sleep(600)
            day, done_today = datetime.now().date(), 0
        if i:
            time.sleep(GAP_S)
        listen.last = None
        try:
            res = asyncio.run(IngestionPipeline(settings, transcriber=listen).run([url])).results[0]
        except Exception as exc:
            res, err = None, f"{type(exc).__name__}: {exc}"
        else:
            err = res.fetch_error or ""
        if res is not None and res.fetch_status is FetchStatus.LOGIN_WALL or any(w in err.lower() for w in STOP_WORDS):
            log(f"STOP for good: Instagram pushed back on reel {i + 1} ({res.fetch_status.value if res else 'error'})")
            return
        if res is None or res.fetch_status is not FetchStatus.OK:
            fails += 1
            log(f"{code} {split}: fetch failed ({res.fetch_status.value if res else 'error'}) [{fails} in a row]")
            if fails >= MAX_FAILS:
                log(f"STOP: {MAX_FAILS} failed fetches in a row")
                return
            continue
        fails = 0
        done_today += 1
        got = listen.last or {"had_video": False, "has_audio": False, "transcript": None}
        with OUT.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"code": code, "split": split, **got}, ensure_ascii=False) + "\n")
        if (i + 1) % 10 == 0:
            rows = jsonl(OUT)
            log(f"progress: {i + 1}/{len(queue)} · with speech {sum(bool(r['transcript']) for r in rows)} · "
                f"silent {sum(r['had_video'] and not r['has_audio'] for r in rows)} · no video {sum(not r['had_video'] for r in rows)}")
    log("all done")


if __name__ == "__main__":
    main()
