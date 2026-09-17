"""Bot-side minting of the catalog API's tenant tokens.

Mirrors src/ingestion/serving/tenant_auth.py — the payload format and env var
MUST stay in step with it. They are duplicated rather than imported because the
bot ships as its own Docker image (`build: ./app`, `COPY . .`), so it cannot see
`src/`. Same reason cards.py mirrors discord_format.py's emoji table.

The bot is the only minter: it derives the tenant key from the real Discord
context (guild_key) and signs it, so the catalog API can tell a genuine call
from anything else that can reach the port.
"""

from __future__ import annotations

import hmac
import os
from hashlib import sha256

ENV_VAR = "SPOTBOT_SIGNING_KEY"

SCOPE_READ = "r"
SCOPE_WRITE = "rw"


def _key() -> bytes:
    raw = os.environ.get(ENV_VAR, "")
    if not raw:
        raise RuntimeError(
            f"{ENV_VAR} is not set - the bot cannot sign catalog requests. "
            "Add it to .env (see .env.example)."
        )
    return raw.encode()


def mint_token(guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = "") -> str:
    payload = f"v1\n{scope}\n{guild_id}\n{user_id}".encode()
    return hmac.new(_key(), payload, sha256).hexdigest()


def tenant_headers(guild_id: str, *, user_id: str = "") -> dict[str, str]:
    """Auth headers for one catalog API call.

    The empty tenant is the legacy single-tenant catalog, which the API leaves
    open, so we send nothing for it and avoid a hard failure when no key is set.
    """
    if not str(guild_id or ""):
        return {}
    return {"X-Tenant-Token": mint_token(str(guild_id), user_id=str(user_id))}
