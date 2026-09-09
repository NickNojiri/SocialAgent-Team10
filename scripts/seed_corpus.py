"""Turn a list of reel URLs into extraction label rows to hand-check.

Runs on your machine (needs the authed IG session from scripts/pull_dm_reels.py).
For each URL it fetches the post, runs the current extractor, and writes a
fixtures/labels.jsonl-shaped row with `predicted` filled and `gold` seeded to the
same values — so labelling is "fix the ones that are wrong", not "type everything".

    python scripts/seed_corpus.py reels.txt --out fixtures/labels.new.jsonl

Then open the .jsonl and for each row:
  - prediction right  -> set every verdict field to "right"
  - prediction wrong  -> fix `gold`, set that verdict to "wrong" / "missing"
  - not a place       -> set gold.in_catalog=false, verdict.venue="wrong"
Merge the reviewed rows into fixtures/labels.jsonl and rerun `python -m src.ingestion.eval`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# IngestionSettings reads IG_USERNAME/IG_PASSWORD from the process env, not .env —
# load it here so the authed fetch path actually has credentials.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
except ModuleNotFoundError:
    pass

from src.ingestion.config import IngestionSettings          # noqa: E402
from src.ingestion.pipeline.normalizer import normalize      # noqa: E402
from src.ingestion.sources.ig_authed import AuthedInstagramSource  # noqa: E402


def _row(url: str, raw, cand: dict) -> dict:
    geo = cand.get("geo")
    place = ""
    if geo is not None:
        place = (getattr(geo, "raw_location_text", None)
                 or (getattr(geo, "place_names", None) or [""])[-1] or "")
    cat = getattr(cand.get("category"), "value", cand.get("category"))
    pred = {"venue": cand.get("venue_name"), "city": place or None, "category": cat}
    return {
        "url": url,
        "input": {
            "caption": getattr(raw, "caption", None) or "",
            "og_title": getattr(raw, "title", None) or "",
            "handle": getattr(raw, "author_handle", None) or "",
            "author_name": getattr(raw, "author_name", None) or "",
            "ocr": getattr(raw, "frame_text", None) or "",
            "transcript": getattr(raw, "transcript", None) or "",
            "hashtags": list(getattr(raw, "hashtags", None) or []),
            # the IG location tag — so offline re-extraction is faithful
            "venue_candidate": getattr(raw, "venue_candidate", None) or "",
            "location_text": getattr(raw, "location_text", None) or "",
            "lat": getattr(raw, "lat", None),
            "lng": getattr(raw, "lng", None),
        },
        "predicted": pred,
        "gold": {**pred, "in_catalog": True},          # seed = prediction; you correct it
        "verdict": {"venue": "needs_review", "city": "needs_review", "category": "needs_review"},
        "note": "",
        "_confidence": {
            "venue_confidence": cand.get("venue_confidence"),
            "venue_slot": cand.get("venue_slot"),
        },
        "input_fidelity": "authed_fetch",
        "needs_recapture": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("urls", type=Path, help="file with one reel URL per line")
    ap.add_argument("--out", type=Path, default=REPO / "fixtures" / "labels.new.jsonl")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between fetches (be polite)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip URLs whose shortcode is already in fixtures/labels.jsonl")
    ap.add_argument("--transcribe", action="store_true",
                    help="also Whisper the reel audio into input.transcript "
                         "(needs faster-whisper + ffmpeg; set WHISPER_MODEL=tiny)")
    args = ap.parse_args()

    transcriber = None
    if args.transcribe:
        from src.ingestion.pipeline.transcriber import Transcriber
        transcriber = Transcriber(IngestionSettings())

    urls = [u.strip() for u in args.urls.read_text().splitlines() if u.strip()]
    if args.skip_existing:
        import json as _j
        base = REPO / "fixtures" / "labels.jsonl"
        # split on "\n" only — captions can contain U+2028/U+2029, which
        # str.splitlines() treats as line breaks and would shred valid JSONL rows.
        done = {(_j.loads(l)["url"]).rstrip("/").split("/")[-1]
                for l in base.read_text().split("\n") if l.strip()} if base.exists() else set()
        before = len(urls)
        urls = [u for u in urls if u.rstrip("/").split("/")[-1] not in done]
        print(f"skip-existing: {before - len(urls)} already labeled, {len(urls)} new", file=sys.stderr)
    src = AuthedInstagramSource(IngestionSettings())

    rows, failed = [], 0
    for i, url in enumerate(urls, 1):
        try:
            raw = src.fetch_url(url)
        except Exception as exc:  # noqa: BLE001
            raw = None
            print(f"  [{i}/{len(urls)}] {url}  fetch error: {type(exc).__name__}", file=sys.stderr)
        if raw is None:
            failed += 1
            rows.append({"url": url, "input": {}, "predicted": {}, "gold": {},
                         "verdict": {}, "note": "FETCH FAILED — recapture", "needs_recapture": True})
            print(f"  [{i}/{len(urls)}] {url}  -> fetch failed", file=sys.stderr)
        else:
            if transcriber is not None and getattr(raw, "video_url", None):
                try:
                    raw.transcript = transcriber.transcribe_url(raw.video_url) or raw.transcript
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{i}] transcribe failed: {type(exc).__name__}", file=sys.stderr)
            cand = normalize(raw)
            rows.append(_row(url, raw, cand))
            print(f"  [{i}/{len(urls)}] {url}  -> venue={cand.get('venue_name')!r} "
                  f"conf={cand.get('venue_confidence')} slot={cand.get('venue_slot')}"
                  f"{' [t]' if getattr(raw, 'transcript', None) else ''}", file=sys.stderr)
        time.sleep(args.sleep)

    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(f"\n{len(rows)} rows ({failed} fetch-failed) -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
