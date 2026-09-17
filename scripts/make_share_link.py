"""Mint a read-only /share link for one guild's catalog.

    python scripts/make_share_link.py <guild_id> [base_url]

The token is read-scoped, so the link can never delete or vote. Rotating
SPOTBOT_SIGNING_KEY revokes every link ever issued. The bot's /share command does
the same thing from inside Discord; this is for operators.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.serving.tenant_auth import SCOPE_READ, mint_token  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    guild_id = sys.argv[1]
    base = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else "http://localhost:8010"
    try:
        token = mint_token(guild_id, scope=SCOPE_READ)
    except Exception as exc:          # fastapi.HTTPException(503) when the key is unset
        raise SystemExit(
            "SPOTBOT_SIGNING_KEY is not set in this shell. Load .env first "
            f"(or run scripts/ensure_signing_key.py). ({getattr(exc, 'detail', exc)})"
        )
    print(f"{base}/share?guild_id={guild_id}&t={token}")


if __name__ == "__main__":
    main()
