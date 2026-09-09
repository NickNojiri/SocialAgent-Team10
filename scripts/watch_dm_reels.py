"""Flag-gated DM watcher: while data/dm_watch.on exists, poll the burner's DM
threads every --interval seconds, fetch any reels not seen before, run the
extractor, and append seed rows to fixtures/labels.inbox.jsonl for later review.

    touch data/dm_watch.on     # start ingesting
    rm    data/dm_watch.on     # pause (process keeps running, just idles)

    python scripts/watch_dm_reels.py --interval 600 --max-per-cycle 15

Runs on your machine (needs the authed IG session + .env creds). One session,
one IP — do NOT run this while scripts/seed_corpus.py is also going. Any
rate-limit error triggers a long back-off instead of a crash.

Merge when you're ready:
    # de-dupe against the corpus and append the new rows
    python - <<'EOF'
    import json, pathlib
    base = pathlib.Path("fixtures/labels.jsonl")
    have = {json.loads(l)["url"].rstrip("/").split("/")[-1]
            for l in base.read_text().split("\n") if l.strip()}
    add = [l for l in pathlib.Path("fixtures/labels.inbox.jsonl").read_text().split("\n")
           if l.strip() and json.loads(l)["url"].rstrip("/").split("/")[-1] not in have]
    with base.open("a") as f:
        for l in add: f.write(l + "\n")
    print(f"appended {len(add)}")
    EOF
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
except ModuleNotFoundError:
    pass

from pull_dm_reels import _client, _codes_from_any  # noqa: E402
from seed_corpus import _row                          # noqa: E402
from src.ingestion.config import IngestionSettings    # noqa: E402
from src.ingestion.pipeline.normalizer import normalize  # noqa: E402
from src.ingestion.sources.ig_authed import AuthedInstagramSource  # noqa: E402

FLAG = REPO / "data" / "dm_watch.on"
SEEN = REPO / "data" / "dm_seen.json"
INBOX = REPO / "fixtures" / "labels.inbox.jsonl"


def _load_seen() -> set[str]:
    try:
        return set(json.loads(SEEN.read_text()))
    except Exception:  # noqa: BLE001
        return set()


def _save_seen(seen: set[str]) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen)))


def _corpus_codes() -> set[str]:
    out: set[str] = set()
    for name in ("fixtures/labels.jsonl", "fixtures/labels.inbox.jsonl"):
        p = REPO / name
        if p.exists():
            out |= {json.loads(l)["url"].rstrip("/").split("/")[-1]
                    for l in p.read_text().split("\n") if l.strip()}
    return out


def cycle(cl, src, seen: set[str], threads: int, cap: int) -> int:
    codes: set[str] = set()
    for t in cl.direct_threads(amount=threads):
        for m in cl.direct_messages(t.id, amount=200):
            try:
                codes |= _codes_from_any(m)
            except Exception:  # noqa: BLE001, S110
                pass
    new = [c for c in codes if c not in seen and c not in _corpus_codes()][:cap]
    added = 0
    with INBOX.open("a") as f:
        for code in new:
            url = f"https://www.instagram.com/reel/{code}/"
            try:
                raw = src.fetch_url(url)
            except Exception as exc:  # noqa: BLE001
                print(f"  fetch error {code}: {type(exc).__name__}", file=sys.stderr)
                raw = None
            seen.add(code)  # mark seen even on failure so we don't hammer it
            if raw is None:
                continue
            f.write(json.dumps(_row(url, raw, normalize(raw)), ensure_ascii=False) + "\n")
            added += 1
            time.sleep(2)
    _save_seen(seen)
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=600, help="seconds between polls (min 300)")
    ap.add_argument("--max-per-cycle", type=int, default=15)
    ap.add_argument("--threads", type=int, default=5)
    args = ap.parse_args()
    interval = max(300, args.interval)

    src = AuthedInstagramSource(IngestionSettings())
    seen = _load_seen()
    cl = None
    print(f"watching — {FLAG} toggles it on/off · every {interval}s · "
          f"<= {args.max_per_cycle}/cycle -> {INBOX}", file=sys.stderr)

    try:
        while True:
            if not FLAG.exists():
                print("  idle (flag off)", file=sys.stderr)
                time.sleep(interval)
                continue
            if cl is None:
                cl = _client()
            try:
                n = cycle(cl, src, seen, args.threads, args.max_per_cycle)
                print(f"  +{n} reels (seen {len(seen)})", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 — likely a rate limit; back off, don't die
                print(f"  cycle error ({type(exc).__name__}: {exc}); backing off 30 min",
                      file=sys.stderr)
                cl = None
                time.sleep(1800)
                continue
            time.sleep(interval)
    except KeyboardInterrupt:
        _save_seen(seen)
        print("\nstopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
