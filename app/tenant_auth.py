"""Bot-side minting of the catalog API's tenant tokens.

Mirrors src/ingestion/serving/tenant_auth.py — the payload format and env var
MUST stay in step with it. They are duplicated rather than imported because the
bot ships as its own Docker image (`build: ./app`, `COPY . .`), so it cannot see
`src/`. Same reason cards.py mirrors discord_format.py's emoji table.

The bot is the only minter: it derives the tenant key from the real Discord
context (guild_key) and signs it, so the catalog API can tell a genuine call
from anything else that can reach the port.

The bot signs with the current key only. SPOTBOT_SIGNING_KEY_PREVIOUS (a planned
rotation's grace period) is read by the API side alone, which verifies both.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from hashlib import sha256

ENV_VAR = "SPOTBOT_SIGNING_KEY"

SCOPE_READ = "r"
SCOPE_WRITE = "rw"
SCOPE_GUEST = "g"  # event-page invite links (ADR-0008); minted by the admin app

# Token v2 (#30, GitHub #13): v2.<kind>.<jti>.<exp>.<sig>. Kind c = per-call, l = link.
KIND_CALL = "c"
KIND_LINK = "l"
DAY = 86400
MAX_TTL = {
    (SCOPE_WRITE, KIND_CALL): 300,
    (SCOPE_WRITE, KIND_LINK): 30 * DAY,
    (SCOPE_GUEST, KIND_LINK): 14 * DAY,
    (SCOPE_READ, KIND_LINK): 30 * DAY,
}


def _key() -> bytes:
    raw = os.environ.get(ENV_VAR, "")
    if not raw:
        raise RuntimeError(
            f"{ENV_VAR} is not set - the bot cannot sign catalog requests. "
            "Add it to .env (see .env.example)."
        )
    return raw.encode()


def mint_token(guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = "", kind: str | None = None,
               ttl: int | None = None, jti: str | None = None, now: float | None = None) -> str:
    """rw without a user is a per-call token (5 min); everything else is a link."""
    kind = kind or (KIND_CALL if scope == SCOPE_WRITE and not user_id else KIND_LINK)
    if (scope, kind) not in MAX_TTL or (scope, kind) == (SCOPE_WRITE, KIND_LINK) and not user_id:
        raise ValueError(f"no such token: scope {scope!r}, kind {kind!r}")
    ttl = MAX_TTL[(scope, kind)] if ttl is None else min(ttl, MAX_TTL[(scope, kind)])
    jti = jti or secrets.token_hex(16)
    exp = int(now if now is not None else time.time()) + ttl
    payload = f"v2\n{kind}\n{scope}\n{guild_id}\n{user_id}\n{jti}\n{exp}".encode()
    return f"v2.{kind}.{jti}.{exp}.{hmac.new(_key(), payload, sha256).hexdigest()}"


def signed_for(token: str, guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = "") -> bool:
    """Whether `token` was signed with this key for this server, scope and user. No expiry or
    revocation check: that's the API's job. Lets tests check a header without the random id."""
    parts = (token or "").split(".")
    if len(parts) != 5 or parts[0] != "v2" or not parts[3].isdigit():
        return False
    _, kind, jti, exp, sig = parts
    payload = f"v2\n{kind}\n{scope}\n{guild_id}\n{user_id}\n{jti}\n{exp}".encode()
    return hmac.compare_digest(sig, hmac.new(_key(), payload, sha256).hexdigest())


def tenant_headers(guild_id: str, *, user_id: str = "") -> dict[str, str]:
    """Auth headers for one catalog API call.

    The empty tenant is the legacy single-tenant catalog, which the API leaves
    open, so we send nothing for it and avoid a hard failure when no key is set.
    """
    if not str(guild_id or ""):
        return {}
    # Always a 5-minute per-call token, even when it acts for a user (a vote).
    return {"X-Tenant-Token": mint_token(str(guild_id), user_id=str(user_id), kind=KIND_CALL)}
