"""Pull Instagram reel/post links out of the burner account's DMs.

For building the extraction label corpus (docs/EXTRACTION_ACCURACY.md) without a
data-download export: DM every reel you want to yourself (or any thread), then
run this once on your own machine.

    pip install instagrapi python-dotenv
    # .env needs IG_USERNAME + IG_PASSWORD (burner account, never personal)
    python scripts/pull_dm_reels.py                 # scan recent threads -> stdout
    python scripts/pull_dm_reels.py --out reels.txt --threads 5 --per-thread 300

Reuses data/ig_session.json (same session file as the authed fetch path) so
login happens once. Any error on a single message is skipped, never fatal.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_URL_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def _codes_from_any(obj) -> set[str]:
    """Best-effort: pull every reel/post shortcode reachable from a DM message."""
    codes: set[str] = set()
    # 1. typed shares instagrapi models directly
    for attr in ("clip", "media_share", "story_share", "felix_share"):
        node = getattr(obj, attr, None)
        code = getattr(node, "code", None) or getattr(getattr(node, "media", None), "code", None)
        if code:
            codes.add(code)
    # 2. xma / link / text — scan their string form for a URL
    for attr in ("xma_share", "xma_media_share", "link", "text", "item_type"):
        val = getattr(obj, attr, None)
        if val is None:
            continue
        for chunk in (val if isinstance(val, (list, tuple)) else [val]):
            codes.update(_URL_RE.findall(str(chunk)))
    # 3. last-ditch: the whole repr (catches schema drift)
    codes.update(_URL_RE.findall(repr(obj)))
    return codes


def _client():
    from dotenv import load_dotenv  # noqa: PLC0415
    from instagrapi import Client   # noqa: PLC0415
    import os

    load_dotenv(REPO / ".env")
    user, pw = os.getenv("IG_USERNAME"), os.getenv("IG_PASSWORD")
    if not (user and pw):
        sys.exit("set IG_USERNAME + IG_PASSWORD in .env (burner account only)")

    cl = Client()
    session = REPO / "data" / "ig_session.json"
    if session.exists():
        cl.load_settings(session)
    cl.login(user, pw)
    session.parent.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(session)
    return cl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threads", type=int, default=10, help="how many recent DM threads to scan")
    ap.add_argument("--per-thread", type=int, default=200, help="messages to read per thread")
    ap.add_argument("--thread-id", help="scan only this thread id")
    ap.add_argument("--out", type=Path, help="also write the URL list here")
    args = ap.parse_args()

    cl = _client()

    thread_ids = [args.thread_id] if args.thread_id else [
        t.id for t in cl.direct_threads(amount=args.threads)
    ]
    print(f"scanning {len(thread_ids)} thread(s)…", file=sys.stderr)

    codes: set[str] = set()
    for tid in thread_ids:
        try:
            msgs = cl.direct_messages(tid, amount=args.per_thread)
        except Exception as exc:  # noqa: BLE001
            print(f"  thread {tid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        before = len(codes)
        for m in msgs:
            try:
                codes |= _codes_from_any(m)
            except Exception:  # noqa: BLE001, S110
                pass
        print(f"  thread {tid}: +{len(codes) - before} reels", file=sys.stderr)

    urls = sorted(f"https://www.instagram.com/reel/{c}/" for c in codes)
    print("\n".join(urls))
    if args.out:
        args.out.write_text("\n".join(urls) + "\n")
        print(f"\n{len(urls)} urls -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
