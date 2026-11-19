"""Signed tenant tokens for the catalog API (v2: expiry, kind and revocation, #30).

`guild_id` picks which catalog a request touches, so a caller has to prove it may name
that tenant. The bot and this app share SPOTBOT_SIGNING_KEY, so a symmetric HMAC is the
right tool. The token format and rules were agreed on GitHub #13:

    v2.<kind>.<jti>.<exp>.<sig>
    sig = HMAC-SHA256(key, "v2\\n{kind}\\n{scope}\\n{guild}\\n{user}\\n{jti}\\n{exp}")

  * kind `c` is a per-call token (bot -> API), `l` a link (/share, /app, invites).
  * Scope, guild and user are not in the string: the verifier rebuilds them from the
    request, so a token can't be moved to another guild, user or scope.
  * Longest lifetime per scope and kind (MAX_TTL); any other combination is refused, and
    an rw link must be bound to a user.
  * `exp` is checked after the signature, with 60 s of clock skew; a token whose `exp`
    is further out than its maximum is refused too, so a minting bug can't make a token
    that never expires. Expired, forged and revoked tokens all get the same 403.
  * Revoked token ids live in data/revoked_tokens.json ({jti: exp}), checked on every
    verify and re-read when the file changes. The file must exist (setup creates it as
    {}); missing, unreadable or invalid means 503 on every tenant request, so deleting
    it by accident fails closed instead of un-revoking tokens.
  * Operator actions (forget a server, delete a spot, change settings) refuse links:
    a leaked /app link can never wipe a server.
  * v1 tokens (no expiry) are refused unless SPOTBOT_ACCEPT_V1_TOKENS=1, a one-release
    bridge for old /share and /app links.
  * During a key rotation's grace period SPOTBOT_SIGNING_KEY_PREVIOUS also verifies.
    Minting always uses the current key. Missing key: 503, never open.
  * The empty tenant ("") is the legacy single-tenant catalog for the local web UI. It
    stays open by design; bind uvicorn to 127.0.0.1.

What stays possible: a captured token replays until it expires (5 minutes for a
per-call token) or is revoked. One-time nonces are out of scope (THREAT_MODEL T2).
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException

ENV_VAR = "SPOTBOT_SIGNING_KEY"
PREVIOUS_ENV_VAR = "SPOTBOT_SIGNING_KEY_PREVIOUS"
ACCEPT_V1_ENV_VAR = "SPOTBOT_ACCEPT_V1_TOKENS"
REVOKED_ENV_VAR = "SPOTBOT_REVOKED_TOKENS"
REVOKED_DEFAULT = Path(__file__).resolve().parents[3] / "data" / "revoked_tokens.json"
LEDGER_DEFAULT = REVOKED_DEFAULT.with_name("minted_links.jsonl")

SCOPE_READ = "r"
SCOPE_WRITE = "rw"
# An invite link for one server's event pages (ADR-0008): read the spots, and RSVP or
# vote in a "find a time" poll under a typed name. Nothing else: no capture, edit or delete.
SCOPE_GUEST = "g"

KIND_CALL = "c"
KIND_LINK = "l"
DAY = 86400
MAX_TTL = {
    (SCOPE_WRITE, KIND_CALL): 300,
    (SCOPE_WRITE, KIND_LINK): 30 * DAY,
    (SCOPE_GUEST, KIND_LINK): 14 * DAY,
    (SCOPE_READ, KIND_LINK): 30 * DAY,
}
SKEW = 60
# \Z, not $: "$" also matches before a trailing newline, which would let "…\n" through.
TOKEN_RE = re.compile(r"^v2\.([cl])\.([0-9a-f]{32})\.([0-9]{1,12})\.([0-9a-f]{64})\Z")
_V1_RE = re.compile(r"^[0-9a-f]{64}\Z")


def _key() -> bytes:
    raw = os.environ.get(ENV_VAR, "")
    if not raw:
        # Fail closed: without a key we cannot tell tenants apart.
        raise HTTPException(
            status_code=503,
            detail=f"{ENV_VAR} is not set - the catalog API cannot authorize tenants",
        )
    return raw.encode()


def _verify_keys() -> list[bytes]:
    """The current key, then the previous one while a rotation's grace period runs."""
    keys = [_key()]
    previous = os.environ.get(PREVIOUS_ENV_VAR, "").encode()
    if previous and previous != keys[0]:
        keys.append(previous)
    return keys


def _payload(kind: str, scope: str, guild_id: str, user_id: str, jti: str, exp: int) -> bytes:
    # Newline-separated and version-prefixed so no field can be smuggled into another.
    return f"v2\n{kind}\n{scope}\n{guild_id}\n{user_id}\n{jti}\n{exp}".encode()


def _sig(key: bytes, kind: str, scope: str, guild_id: str, user_id: str, jti: str, exp: int) -> str:
    return hmac.new(key, _payload(kind, scope, str(guild_id), str(user_id), jti, exp), sha256).hexdigest()


def default_kind(scope: str, user_id: str = "") -> str:
    """rw without a user is a per-call token; everything else minted here is a link."""
    return KIND_CALL if scope == SCOPE_WRITE and not user_id else KIND_LINK


def mint_token(guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = "", kind: str | None = None,
               ttl: int | None = None, jti: str | None = None, now: float | None = None) -> str:
    kind = kind or default_kind(scope, user_id)
    if (scope, kind) not in MAX_TTL or (scope, kind) == (SCOPE_WRITE, KIND_LINK) and not user_id:
        raise ValueError(f"no such token: scope {scope!r}, kind {kind!r}" + (" without a user" if not user_id else ""))
    ttl = MAX_TTL[(scope, kind)] if ttl is None else min(ttl, MAX_TTL[(scope, kind)])
    jti = jti or secrets.token_hex(16)
    exp = int(now if now is not None else time.time()) + ttl
    return f"v2.{kind}.{jti}.{exp}.{_sig(_key(), kind, scope, guild_id, user_id, jti, exp)}"


def parse(token: str | None) -> tuple[str, str, int] | None:
    """(kind, jti, exp) of a well-formed v2 token, else None. Nothing here is trusted yet."""
    m = TOKEN_RE.match(token or "")
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


# ── revocation list ──────────────────────────────────────────────────────────

_revoked_lock = threading.Lock()
_revoked_cache: dict = {"path": None, "mtime": None, "jtis": {}}


def revoked_path() -> Path:
    return Path(os.environ.get(REVOKED_ENV_VAR) or REVOKED_DEFAULT)


def _unavailable(why: str) -> HTTPException:
    return HTTPException(status_code=503, detail=f"token revocation list {why} - refusing every tenant request")


def revoked_jtis() -> dict[str, int]:
    """The revoked {jti: exp}, re-read when the file changes. Fails closed (503)."""
    path = revoked_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        raise _unavailable("is missing (run scripts/ensure_signing_key.py)") from None
    with _revoked_lock:
        c = _revoked_cache
        if c["path"] == str(path) and c["mtime"] == mtime:
            return c["jtis"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            jtis = {str(k): int(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError):
            raise _unavailable("can't be read") from None
        c.update(path=str(path), mtime=mtime, jtis=jtis)
        return jtis


def revoke(token_or_jti: str, *, exp: int | None = None, path: Path | None = None, now: float | None = None) -> str:
    """Add one token id to the list (and drop entries whose tokens have expired anyway)."""
    parsed = parse(token_or_jti)
    jti = parsed[1] if parsed else token_or_jti
    if not re.fullmatch(r"[0-9a-f]{32}", jti):
        raise ValueError("not a v2 token or token id")
    exp = parsed[2] if parsed else (exp or int(time.time()) + 30 * DAY)
    path = path or revoked_path()
    now = int(now if now is not None else time.time())
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = {k: v for k, v in data.items() if int(v) + SKEW >= now}
    data[jti] = int(exp)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=0, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return jti


def record_link(token: str, *, scope: str, guild_id: str, user_id: str = "", path: Path | None = None) -> None:
    """Note a minted link (never its signature) so an operator can find and revoke it."""
    parsed = parse(token)
    if not parsed or parsed[0] != KIND_LINK:
        return
    path = path or Path(os.environ.get("SPOTBOT_MINTED_LINKS") or LEDGER_DEFAULT)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"jti": parsed[1], "scope": scope, "guild": str(guild_id), "user": str(user_id),
                                "exp": parsed[2], "minted_at": int(time.time())}) + "\n")
    except OSError:
        pass                                    # the ledger is a convenience, never a reason to fail


# ── verify ───────────────────────────────────────────────────────────────────

def _accept_v1() -> bool:
    return os.environ.get(ACCEPT_V1_ENV_VAR, "").strip() == "1"


def _verify_v1(token: str, guild_id: str, scope: str, user_id: str) -> bool:
    if not _V1_RE.match(token):
        return False
    payload = f"v1\n{scope}\n{guild_id}\n{user_id}".encode()
    matched = False
    for key in _verify_keys():
        matched |= hmac.compare_digest(token, hmac.new(key, payload, sha256).hexdigest())
    return matched


def verify_token(token: str | None, guild_id: str, *, scope: str = SCOPE_WRITE, user_id: str = "",
                 kinds: tuple[str, ...] = (KIND_CALL, KIND_LINK), now: float | None = None) -> bool:
    if not token:
        return False
    parsed = parse(token)
    if parsed is None:
        return _accept_v1() and _verify_v1(token, str(guild_id), scope, str(user_id))
    kind, jti, exp = parsed
    sig = token.rsplit(".", 1)[1]
    # Check every key rather than stopping at the first, so timing doesn't reveal which signed it.
    matched = False
    for key in _verify_keys():
        matched |= hmac.compare_digest(sig, _sig(key, kind, scope, guild_id, user_id, jti, exp))
    if not matched or kind not in kinds:
        return False
    limit = MAX_TTL.get((scope, kind))
    if limit is None or (scope, kind) == (SCOPE_WRITE, KIND_LINK) and not user_id:
        return False
    t = time.time() if now is None else now
    if exp + SKEW < t or exp > t + limit + SKEW:
        return False
    return jti not in revoked_jtis()


def authorize(guild_id: str, token: str | None, *, need: str = SCOPE_WRITE, user_id: str = "",
              operator: bool = False) -> None:
    """Raise 403 unless `token` proves the caller may act on this tenant.

    A write token also satisfies a read or guest requirement; a guest token satisfies
    reads and guest actions (RSVP, poll votes); a read token satisfies only reads.
    `operator=True` (forget, delete, settings) accepts only per-call tokens, never a link.
    """
    if not str(guild_id or ""):
        return  # legacy single-tenant catalog - see the module docstring
    _key()          # no key: 503 for every tenant request, whatever the token looks like
    revoked_jtis()  # every tenant request needs the list: missing or broken -> 503, never open
    allowed = {
        SCOPE_WRITE: (SCOPE_WRITE,),
        SCOPE_GUEST: (SCOPE_GUEST, SCOPE_WRITE),
    }.get(need, (SCOPE_READ, SCOPE_GUEST, SCOPE_WRITE))
    kinds = (KIND_CALL,) if operator else (KIND_CALL, KIND_LINK)
    if any(verify_token(token, guild_id, scope=s, user_id=user_id, kinds=kinds) for s in allowed):
        return
    raise HTTPException(status_code=403, detail="invalid or missing tenant token")
