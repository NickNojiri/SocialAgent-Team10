"""Track D #30: token v2 — expiry, kind, revocation and replay (contract on GitHub #13).

Every case here is an attack the API must refuse, or the control showing the honest
path still works. They run against the real admin app with a stub catalog, offline.
"""
import json

import pytest
from fastapi import HTTPException

from src.ingestion.serving import tenant_auth as ta
from src.ingestion.serving.tenant_auth import (
    KIND_CALL,
    KIND_LINK,
    SCOPE_GUEST,
    SCOPE_READ,
    SCOPE_WRITE,
    mint_token,
)
from test_admin_authz import EVENT, KEY, ME, MINE, client, hdr  # noqa: F401  (client is a fixture)

OLD = "1" * 64
NOW = 1_800_000_000


def forge(guild, *, scope=SCOPE_WRITE, user="", kind=KIND_CALL, exp, key=KEY, jti="ab" * 16):
    """A token signed with a real key but claims the minter would never write."""
    return f"v2.{kind}.{jti}.{exp}.{ta._sig(key.encode(), kind, scope, guild, user, jti, exp)}"


def read(c, token):
    return c.get(f"/api/events?guild_id={MINE}", headers=hdr(token)).status_code


# ── valid, expired, tampered ─────────────────────────────────────────────────

def test_a_fresh_token_works(client):
    assert read(client, mint_token(MINE)) == 200


def test_an_expired_token_is_refused_like_a_forged_one(client, monkeypatch):
    old = mint_token(MINE, now=1_000_000)                          # expired long ago
    r = client.get(f"/api/events?guild_id={MINE}", headers=hdr(old))
    forged = client.get(f"/api/events?guild_id={MINE}", headers=hdr(f"v2.c.{'0' * 32}.9999999999.{'0' * 64}"))
    assert r.status_code == forged.status_code == 403
    assert r.json() == forged.json() == {"detail": "invalid or missing tenant token"}


def test_clock_skew_of_a_minute_is_allowed(monkeypatch):
    monkeypatch.setenv(ta.ENV_VAR, KEY)
    token = mint_token(MINE, now=NOW)                               # exp = NOW + 300
    assert ta.verify_token(token, MINE, now=NOW + 300 + 59)
    assert not ta.verify_token(token, MINE, now=NOW + 300 + 61)


@pytest.mark.parametrize("change", ["kind", "jti", "exp", "sig"])
def test_any_edit_to_a_token_breaks_it(client, change):
    v, kind, jti, exp, sig = mint_token(MINE, user_id=ME, kind=KIND_LINK).split(".")
    edited = {"kind": [v, "c", jti, exp, sig], "jti": [v, kind, "f" * 32, exp, sig],
              "exp": [v, kind, jti, str(int(exp) - 1), sig], "sig": [v, kind, jti, exp, "0" * 64]}[change]
    r = client.post("/api/invite", headers=hdr(".".join(edited)), json={"guild_id": MINE, "user_id": ME})
    assert r.status_code == 403


def test_malformed_tokens_never_reach_the_hmac(client, monkeypatch):
    calls = []
    monkeypatch.setattr(ta, "_sig", lambda *a: calls.append(a) or "0" * 64)
    for bad in ["v2.x.%s.1.%s" % ("0" * 32, "0" * 64), "v2.c.short.1.sig", "v2.c.%s.1.%s\n" % ("0" * 32, "0" * 64),
                "v3." + "0" * 100, "a" * 5000]:
        assert read(client, bad) == 403
    assert calls == []


# ── kind and maximum lifetime ────────────────────────────────────────────────

def test_a_per_call_token_that_claims_ten_minutes_is_refused(client):
    import time
    assert read(client, forge(MINE, exp=int(time.time()) + 600)) == 403


def test_a_link_further_out_than_its_maximum_is_refused(client):
    import time
    far = int(time.time()) + 31 * 86400
    assert read(client, forge(MINE, scope=SCOPE_READ, kind=KIND_LINK, exp=far)) == 403


def test_an_rw_link_must_be_bound_to_a_user(client):
    import time
    token = forge(MINE, kind=KIND_LINK, exp=int(time.time()) + 3600)
    assert client.post("/api/manual", headers=hdr(token), json={"guild_id": MINE, "venue": "X", "theme": "a b c"}).status_code == 403
    with pytest.raises(ValueError):
        mint_token(MINE, kind=KIND_LINK)


def test_a_guest_per_call_token_does_not_exist(client):
    import time
    assert read(client, forge(MINE, scope=SCOPE_GUEST, kind=KIND_CALL, exp=int(time.time()) + 60)) == 403


@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/forget", {"guild_id": MINE, "confirm": MINE}),
    ("delete", f"/api/events/{EVENT}?guild_id={MINE}", None),
    ("put", "/api/settings", {"guild_id": MINE, "home_city": "Irvine"}),
])
def test_an_app_link_can_never_run_an_operator_action(client, method, path, body):
    link = mint_token(MINE, user_id=ME)                              # the /app link: rw, kind l, 30 days
    kw = {"headers": hdr(link)} | ({"json": body} if body else {})
    assert getattr(client, method)(path, **kw).status_code == 403
    assert client.post("/api/invite", headers=hdr(link), json={"guild_id": MINE, "user_id": ME}).status_code == 200


# ── revocation ───────────────────────────────────────────────────────────────

def test_revoking_one_token_leaves_the_others_working(client):
    a, b = mint_token(MINE), mint_token(MINE)
    assert read(client, a) == read(client, b) == 200
    ta.revoke(a)
    assert read(client, a) == 403                                    # replaying it now fails at once
    assert read(client, b) == 200


def test_revoking_a_share_link_by_its_id(client):
    link = mint_token(MINE, scope=SCOPE_READ)
    ta.revoke(ta.parse(link)[1], exp=ta.parse(link)[2])
    assert client.get(f"/share?guild_id={MINE}&t={link}").status_code == 403


def test_no_revocation_list_means_503(client, monkeypatch, tmp_path):
    monkeypatch.setenv(ta.REVOKED_ENV_VAR, str(tmp_path / "nope.json"))
    assert read(client, mint_token(MINE)) == 503
    assert read(client, "not even a token") == 503                   # every tenant request, not only valid ones


def test_an_empty_list_works_and_a_broken_one_fails_closed(client, monkeypatch, tmp_path):
    path = tmp_path / "revoked.json"
    monkeypatch.setenv(ta.REVOKED_ENV_VAR, str(path))
    path.write_text("{}", encoding="utf-8")
    assert read(client, mint_token(MINE)) == 200
    path.write_text("{not json", encoding="utf-8")
    assert read(client, mint_token(MINE)) == 503


def test_deleting_the_list_after_a_revocation_fails_closed(client, monkeypatch, tmp_path):
    path = tmp_path / "revoked.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(ta.REVOKED_ENV_VAR, str(path))
    stolen = mint_token(MINE)
    ta.revoke(stolen)
    path.unlink()
    assert read(client, stolen) == 503                               # not quietly un-revoked


def test_revoke_prunes_entries_that_expired_anyway(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"aa" * 16: 1}), encoding="utf-8")
    ta.revoke("bb" * 16, exp=NOW + 10, path=path, now=NOW)
    assert json.loads(path.read_text(encoding="utf-8")) == {"bb" * 16: NOW + 10}


# ── v1 and key rotation ──────────────────────────────────────────────────────

def _v1(scope=SCOPE_WRITE, key=KEY):
    import hmac
    from hashlib import sha256
    return hmac.new(key.encode(), f"v1\n{scope}\n{MINE}\n".encode(), sha256).hexdigest()


def test_v1_tokens_are_refused_by_default(client):
    assert read(client, _v1()) == 403


def test_v1_tokens_work_only_with_the_one_release_bridge(client, monkeypatch):
    monkeypatch.setenv(ta.ACCEPT_V1_ENV_VAR, "1")
    assert read(client, _v1()) == 200


def test_rotation_grace_then_end_of_grace(client, monkeypatch):
    monkeypatch.setenv(ta.ENV_VAR, OLD)
    old_token = mint_token(MINE)                                     # signed before the rotation
    revoked_old = mint_token(MINE)
    monkeypatch.setenv(ta.ENV_VAR, KEY)
    monkeypatch.setenv(ta.PREVIOUS_ENV_VAR, OLD)                     # grace period
    ta.revoke(revoked_old)
    assert read(client, old_token) == 200
    assert read(client, revoked_old) == 403                          # revocation applies whichever key signed it
    monkeypatch.delenv(ta.PREVIOUS_ENV_VAR)                          # --end-grace
    assert read(client, old_token) == 403


def test_missing_key_is_503_whatever_the_token(monkeypatch):
    monkeypatch.delenv(ta.ENV_VAR, raising=False)
    with pytest.raises(HTTPException) as err:
        ta.authorize(MINE, "anything")
    assert err.value.status_code == 503


def test_revoke_script_kills_every_ledgered_link_for_one_server(client, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("revoke_token", Path(__file__).parent / "scripts" / "revoke_token.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    invite = client.post("/api/invite", headers=hdr(mint_token(MINE)), json={"guild_id": MINE}).json()["token"]
    assert read(client, invite) == 200
    rt.main(["--guild", MINE])
    assert read(client, invite) == 403
    ledger = Path(ta.os.environ["SPOTBOT_MINTED_LINKS"]).read_text(encoding="utf-8")
    assert ta.parse(invite)[1] in ledger and invite.rsplit(".", 1)[1] not in ledger   # never the signature
