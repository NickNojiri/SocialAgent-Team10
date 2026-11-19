"""Revoke one tenant token or link at once (#30).

    python scripts/revoke_token.py <token or token id>     # one token
    python scripts/revoke_token.py --guild 1234             # every link minted for one server

Adds the token id to data/revoked_tokens.json. The API re-reads the file on the next
request, so no restart is needed. Only that token stops working; every other token for
the server keeps going. Links minted by the admin app and make_share_link.py are listed
in data/minted_links.jsonl, which is what --guild reads. To kill every token at once,
rotate the key without a grace period (scripts/rotate_signing_key.py).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.serving import tenant_auth as ta  # noqa: E402


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("token", nargs="?", help="a v2 token, or its 32-hex-digit id")
    ap.add_argument("--guild", help="revoke every unexpired link the ledger lists for this server")
    args = ap.parse_args(argv)
    if not args.token and not args.guild:
        ap.error("give a token or --guild")
    if args.token:
        print(f"revoked {ta.revoke(args.token)}")
    if args.guild:
        ledger = Path(ta.os.environ.get("SPOTBOT_MINTED_LINKS") or ta.LEDGER_DEFAULT)
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").split("\n") if line.strip()] \
            if ledger.exists() else []
        live = [r for r in rows if r["guild"] == args.guild and r["exp"] > time.time()]
        for r in live:
            ta.revoke(r["jti"], exp=r["exp"])
        print(f"revoked {len(live)} link(s) for server {args.guild}")


if __name__ == "__main__":
    main()
