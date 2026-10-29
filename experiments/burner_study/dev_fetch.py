"""Burner job for the v2 plan, in order:
  1. every dev reel: video -> transcript (Whisper small), cover -> on-screen text (OCR), and the
     poster's account info
  2. account info for the posters of train reels (so a model can LEARN what account type means)

Account info kept: account type (personal/business/creator), business category, display name,
bio, verified. Cached per username in data/experiments/accounts.jsonl, never fetched twice.

Pacing: at most 15 Instagram calls in any 10-minute window (one call = one media fetch or one
profile fetch). Stops for good (no retry) on a failed login, any challenge / login-required /
please-wait / rate-limit error, or 3 failed fetches in a row. Resumable. Logs counts only.
"""
import json
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]
os.chdir(WORK)
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(WORK / "scripts"))
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

import watch_dm_reels as w  # noqa: E402  (loads WORK/.env)
from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.ocr import ImageTextReader  # noqa: E402
from src.ingestion.pipeline.transcriber import Transcriber  # noqa: E402
from src.ingestion.sources.ig_authed import AuthedInstagramSource  # noqa: E402

EXP = WORK / "data" / "experiments"
DEV_OUT, ACCOUNTS = EXP / "dev_av.jsonl", EXP / "accounts.jsonl"
CALLS, WINDOW = 15, 600
STOP_WORDS = ("challenge", "loginrequired", "login_required", "pleasewait", "please wait", "ratelimit",
              "429", "feedback_required", "checkpoint")
recent: deque = deque()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def pace():
    """Block until another call keeps us within CALLS per WINDOW seconds."""
    while len(recent) >= CALLS:
        wait = recent[0] + WINDOW - time.time()
        if wait <= 0:
            recent.popleft()
            continue
        log(f"pacing: waiting {wait / 60:.1f} min")
        time.sleep(wait + 1)
    recent.append(time.time())


def must_stop(exc) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in STOP_WORDS)


def codes_in(p):
    if not p.exists():
        return set()
    return {json.loads(l)["code"] for l in p.read_text(encoding="utf-8").split("\n") if l.strip()}


def fresh_fields(snap):
    """The post as Instagram serves it now: the FULL caption (some stored captions are only the
    first line), location tag + coordinates, display name, mentions."""
    return {"caption": snap.caption, "location_text": snap.location_text, "lat": snap.lat, "lng": snap.lng,
            "author_name": snap.author_name, "mentions": snap.mentions}


def known_accounts():
    if not ACCOUNTS.exists():
        return set()
    return {json.loads(l)["username"].lower() for l in ACCOUNTS.read_text(encoding="utf-8").split("\n") if l.strip()}


def fetch_account(cl, username):
    pace()
    u = cl.user_info_by_username_v1(username)
    row = {"username": username, "account_type": getattr(u, "account_type", None),
           "is_business": getattr(u, "is_business", None), "category": getattr(u, "category", None),
           "category_name": getattr(u, "category_name", None),
           "business_category_name": getattr(u, "business_category_name", None),
           "full_name": getattr(u, "full_name", None), "biography": getattr(u, "biography", None),
           "is_verified": getattr(u, "is_verified", None)}
    with ACCOUNTS.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    if "--wait" in sys.argv:          # restarted mid-run: let the previous process's calls age out
        log("waiting 10 min before the first call (restart)")
        time.sleep(WINDOW)
    rows = ev.load_labels()
    dev = [r for r in rows if ev.split_of(r) == "dev"]
    done = codes_in(DEV_OUT)
    todo = [r for r in dev if ev._code(r["url"]) not in done]
    log(f"phase 1: {len(todo)} dev reels to do ({len(done)} done)")
    settings = IngestionSettings(transcribe_enabled=True, whisper_model="small")
    try:
        cl = w._client()
    except Exception as exc:  # noqa: BLE001
        log(f"LOGIN FAILED, stopping: {type(exc).__name__}: {exc}")
        sys.exit(1)
    src = AuthedInstagramSource(settings)
    src._client = cl
    stt, ocr = Transcriber(settings), ImageTextReader(settings)
    have = known_accounts()
    misses = 0

    for n, r in enumerate(todo, 1):
        pace()
        snap = src.fetch_url(r["url"])           # degrades to None on any error (logged by the source)
        if snap is None:
            misses += 1
            log(f"dev {n}: fetch failed ({misses} in a row)")
            if misses >= 3:
                log("3 failed fetches in a row, stopping (no retry)")
                sys.exit(1)
            continue
        misses = 0
        video = getattr(snap, "video_url", None)
        transcript = stt.transcribe_url(video) if video else None
        screen = ocr.read_url(snap.image_url)
        author = (snap.author_handle or (r.get("input") or {}).get("handle") or "").lstrip("@")
        with DEV_OUT.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"code": ev._code(r["url"]), "author": author, "had_video": bool(video),
                                "transcript": transcript, "ocr": screen, **fresh_fields(snap)}, ensure_ascii=False) + "\n")
        log(f"dev {n}: video={bool(video)} transcript={len(transcript or '')} ocr={len(screen or '')}")
        if author and author.lower() not in have:
            try:
                fetch_account(cl, author)
                have.add(author.lower())
            except Exception as exc:  # noqa: BLE001
                if must_stop(exc):
                    log(f"STOPPING (no retry): {type(exc).__name__}")
                    sys.exit(1)
                log(f"dev {n}: account fetch failed: {type(exc).__name__}")

    # phase 1b: the full post for (a) dev reels done before captions were saved, (b) train reels whose
    # stored caption is only the first line or flagged for recapture. Test is left as-is on purpose.
    RECAP = EXP / "recapture.jsonl"
    have_fresh = {json.loads(l)["code"] for l in DEV_OUT.read_text(encoding="utf-8").split("\n")
                  if l.strip() and "caption" in json.loads(l)} | codes_in(RECAP)
    redo = [r for r in dev if ev._code(r["url"]) not in have_fresh]
    redo += [r for r in rows if ev.split_of(r) == "train" and ev._code(r["url"]) not in have_fresh
             and (r.get("input_fidelity") == "first_line_only" or r.get("needs_recapture"))]
    log(f"phase 1b: {len(redo)} posts to re-read (captions only)")
    misses = 0
    for n, r in enumerate(redo, 1):
        pace()
        snap = src.fetch_url(r["url"])
        if snap is None:
            misses += 1
            log(f"1b {n}: fetch failed ({misses} in a row)")
            if misses >= 3:
                log("3 failed fetches in a row, stopping (no retry)")
                sys.exit(1)
            continue
        misses = 0
        with RECAP.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"code": ev._code(r["url"]), **fresh_fields(snap)}, ensure_ascii=False) + "\n")
        if n % 10 == 0:
            log(f"phase 1b: {n}/{len(redo)}")

    # phase 2: train posters (gold first, then the burner reels), most reels first
    sys.path.insert(0, str(WORK / "experiments" / "burner_study"))
    from collections import Counter
    burner = [json.loads(l) for l in (WORK / "fixtures" / "labels.inbox.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
    gold_train = Counter((r.get("input") or {}).get("handle", "").lstrip("@").lower()
                         for r in rows if ev.split_of(r) == "train")
    burner_authors = Counter((r.get("input") or {}).get("handle", "").lstrip("@").lower() for r in burner)
    order = [a for a, _ in gold_train.most_common()] + [a for a, _ in burner_authors.most_common()]
    order = [a for a in dict.fromkeys(order) if a and a not in have]
    log(f"phase 2: {len(order)} train/burner posters to look up")
    fails = 0
    for n, a in enumerate(order, 1):
        try:
            fetch_account(cl, a)
            have.add(a)
            fails = 0
            if n % 15 == 0:
                log(f"phase 2: {n}/{len(order)} accounts")
        except Exception as exc:  # noqa: BLE001
            if must_stop(exc):
                log(f"STOPPING (no retry): {type(exc).__name__}")
                sys.exit(1)
            fails += 1
            log(f"phase 2: account {n} failed: {type(exc).__name__} ({fails} in a row)")
            if fails >= 3:
                log("3 failed account lookups in a row, stopping (no retry)")
                sys.exit(1)
    log("all done")


if __name__ == "__main__":
    main()
