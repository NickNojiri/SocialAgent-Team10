"""Signed tenant keys for the catalog API.

`guild_id` picks which catalog a request touches (see
`chroma_sink.collection_for_guild`), but it used to arrive as a plain query/body
field — so anything that could reach :8010 could read, wipe, or stuff votes in
*any* server's catalog just by naming its id.

The bot and this app both run on the operator's machine and share
SPOTBOT_SIGNING_KEY, so a symmetric HMAC is the right tool (not Ed25519 — there
is no second party here). The bot mints a token over the tenant key it derived
from the real Discord context; this app recomputes it. A caller who cannot
produce the HMAC cannot name a tenant.

Votes also bind `user_id`, so a vote cannot be cast — or withdrawn — as someone
else. Read-only share links get a separate `r` scope so a link you send to a
friend cannot delete anything.

Scope and residual risk, stated plainly:
  * A non-empty guild_id ALWAYS requires a valid token.
  * The empty tenant ("") is the legacy single-tenant catalog the local web UI
    uses. It stays open by design; bind uvicorn to 127.0.0.1 (see
    scripts/run_local.ps1) so it is not reachable off-host.
  * Tokens are stable bearer capabilities, not nonces. Whoever captures one
    keeps access to that tenant until SPOTBOT_SIGNING_KEY is rotated.
  * Missing key fails closed (503), never open.
"""

from __future__ import annotations

import hmac
import os
from hashlib import sha256

from fastapi import HTTPException

ENV_VAR = "SPOTBOT_SIGNING_KEY"

SCOPE_READ = "r"
SCOPE_WRITE = "rw"


def _key() -> bytes:
    raw = os.environ.get(ENV_VAR, "")
    if not raw:
        # Fail closed: without a key we cannot tell tenants apart, so we refuse
        # rather than fall back to the old trust-the-caller behaviour.
        raise HTTPException(
            status_code=503,
            detail=f"{ENV_VAR} is not set - the catalog API cannot authorize tenants",
        )
    return raw.encode()


def _payload(guild_id: str, scope: str, user_id: str) -> bytes:
    # Newline-separated and version-prefixed so no field can be smuggled into
    # another by choosing a value containing the separator.
    return f"v1\n{scope}\n{guild_id}\n{user_id}".encode()


def mint_token(guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = "") -> str:
    return hmac.new(_key(), _payload(str(guild_id), scope, str(user_id)), sha256).hexdigest()


def verify_token(
    token: str | None, guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = ""
) -> bool:
    if not token:
        return False
    return hmac.compare_digest(
        token, mint_token(guild_id, scope=scope, user_id=user_id)
    )


def authorize(
    guild_id: str,
    token: str | None,
    *,
    need: str = SCOPE_WRITE,
    user_id: str = "",
) -> None:
    """Raise 403 unless `token` proves the caller may act on this tenant.

    A write token also satisfies a read requirement; a read token never
    satisfies a write.
    """
    if not str(guild_id or ""):
        return  # legacy single-tenant catalog - see the module docstring
    allowed = (SCOPE_WRITE,) if need == SCOPE_WRITE else (SCOPE_READ, SCOPE_WRITE)
    if any(verify_token(token, guild_id, scope=s, user_id=user_id) for s in allowed):
        return
    raise HTTPException(status_code=403, detail="invalid or missing tenant token")
