"""Cross-tenant authorization tests for the catalog API (:8010) and the
recommend service (:8003).

Before signed tenant keys, `guild_id` was a plain request field: anything that
could reach the ports could read, wipe, or stuff votes in any guild's catalog by
naming its id. These tests pin that shut, endpoint by endpoint
(docs/THREAT_MODEL.md T2).

The Chroma store is stubbed out — this is about the authorization gate, so a
test must fail on a 200, not on whether the database happened to be reachable.
"""

import time

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
import src.ingestion.serving.app as serving_app
from src.ingestion.serving.capture_limits import CaptureRateLimiter
from src.ingestion.serving.jobs import JobQueue
from src.ingestion.serving.tenant_auth import ENV_VAR, SCOPE_READ, mint_token

KEY = "0" * 64
MINE = "111111111111111111"
THEIRS = "222222222222222222"
ME = "user-me"
YOU = "user-you"
EVENT = "abc123"


class StubCollection:
    """Just enough Chroma for the endpoints to return 200 when authorized."""

    def get(self, ids=None, include=None):
        meta = {"venue_name": "Stub", "votes": 0, "category": "other"}
        if ids is not None:
            return {"ids": list(ids), "metadatas": [dict(meta)], "documents": ["d"]}
        return {"ids": [EVENT], "metadatas": [dict(meta)], "documents": ["d"]}

    def delete(self, ids=None):
        return None

    def update(self, **kwargs):
        return None


class StubSink:
    def __init__(self):
        self.collection = StubCollection()

    def embedder(self, texts):
        return [[0.0] * 4 for _ in texts]

    def add(self, record):
        return "new-id"

    def write(self, result):
        # Only reached if an ingest gets past the gate — which is the bug this
        # suite exists to prevent.
        return None


async def _never_ingest(urls, guild_id, on_stage):
    raise AssertionError("an unauthorized capture reached the pipeline")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(ENV_VAR, KEY)
    monkeypatch.setattr(admin, "_sink_for", lambda guild_id="": StubSink())
    monkeypatch.setattr(admin, "_jobs", JobQueue(_never_ingest))
    monkeypatch.setattr(admin, "_capture_limits", CaptureRateLimiter())
    with TestClient(admin.app) as c:
        yield c


def hdr(token):
    return {"X-Tenant-Token": token} if token else {}


# ── read ─────────────────────────────────────────────────────────────────────


def test_read_with_valid_token(client):
    r = client.get(f"/api/events?guild_id={MINE}", headers=hdr(mint_token(MINE)))
    assert r.status_code == 200


def test_read_with_read_scoped_token(client):
    token = mint_token(MINE, scope=SCOPE_READ)
    assert client.get(f"/api/events?guild_id={MINE}", headers=hdr(token)).status_code == 200


def test_read_without_token_rejected(client):
    assert client.get(f"/api/events?guild_id={MINE}").status_code == 403


def test_read_with_other_guilds_token_rejected(client):
    """The core bug: a token for my guild must not open yours."""
    r = client.get(f"/api/events?guild_id={THEIRS}", headers=hdr(mint_token(MINE)))
    assert r.status_code == 403


def test_dm_stash_is_a_tenant_too(client):
    """DM captures live under 'dm-<user id>' — not the open legacy catalog."""
    assert client.get("/api/events?guild_id=dm-42").status_code == 403
    assert client.get("/api/events?guild_id=dm-42", headers=hdr(mint_token("dm-42"))).status_code == 200


def test_nights_requires_token(client):
    assert client.get(f"/api/nights?guild_id={MINE}").status_code == 403
    assert client.get(f"/api/nights?guild_id={MINE}", headers=hdr(mint_token(MINE))).status_code == 200


# ── delete ───────────────────────────────────────────────────────────────────


def test_delete_with_valid_token(client):
    r = client.delete(f"/api/events/{EVENT}?guild_id={MINE}", headers=hdr(mint_token(MINE)))
    assert r.status_code == 200


def test_delete_without_token_rejected(client):
    assert client.delete(f"/api/events/{EVENT}?guild_id={MINE}").status_code == 403


def test_delete_with_other_guilds_token_rejected(client):
    r = client.delete(f"/api/events/{EVENT}?guild_id={THEIRS}", headers=hdr(mint_token(MINE)))
    assert r.status_code == 403


def test_delete_with_read_scoped_token_rejected(client):
    """A share link must never be able to wipe a catalog."""
    token = mint_token(MINE, scope=SCOPE_READ)
    r = client.delete(f"/api/events/{EVENT}?guild_id={MINE}", headers=hdr(token))
    assert r.status_code == 403


# ── vote / went: signed over the acting user ────────────────────────────────


def vote(client, token, *, guild=MINE, user_id=ME, delta=1):
    return client.post(
        f"/api/events/{EVENT}/vote?guild_id={guild}",
        json={"delta": delta, "user_id": user_id, "user_name": user_id},
        headers=hdr(token),
    )


def test_vote_as_self(client):
    assert vote(client, mint_token(MINE, user_id=ME)).status_code == 200


def test_vote_as_another_user_rejected(client):
    """Token signed for ME must not authorize a vote claiming to be YOU."""
    assert vote(client, mint_token(MINE, user_id=ME), user_id=YOU).status_code == 403


def test_unvote_as_another_user_rejected(client):
    """Same guard on delta=-1 — otherwise you could strip someone's vote."""
    assert vote(client, mint_token(MINE, user_id=ME), user_id=YOU, delta=-1).status_code == 403


def test_vote_without_token_rejected(client):
    assert vote(client, None).status_code == 403


def went(client, token, *, user_id=ME, happened=True):
    return client.post(
        f"/api/events/{EVENT}/went?guild_id={MINE}",
        json={"user_id": user_id, "user_name": user_id, "happened": happened},
        headers=hdr(token),
    )


def test_went_as_self(client):
    assert went(client, mint_token(MINE, user_id=ME)).status_code == 200


def test_went_as_another_user_rejected(client):
    """Two confirmations from different people make a night — no confirming as someone else."""
    assert went(client, mint_token(MINE, user_id=ME), user_id=YOU).status_code == 403
    assert went(client, mint_token(MINE, user_id=ME), user_id=YOU, happened=False).status_code == 403


# ── writes that name the tenant in the body ─────────────────────────────────


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/manual", {"venue": "Casa Loma", "theme": "birria tacos"}),
        (f"/api/events/{EVENT}/edit", {"venue": "Casa Loma", "theme": "birria tacos"}),
        (f"/api/events/{EVENT}/lock", {"channel_id": "1", "end_epoch": 1_800_000_000}),
    ],
)
def test_body_tenant_writes(client, path, body):
    assert client.post(path, json={**body, "guild_id": MINE}).status_code == 403
    assert client.post(path, json={**body, "guild_id": THEIRS}, headers=hdr(mint_token(MINE))).status_code == 403
    read_only = mint_token(MINE, scope=SCOPE_READ)
    assert client.post(path, json={**body, "guild_id": MINE}, headers=hdr(read_only)).status_code == 403
    assert client.post(path, json={**body, "guild_id": MINE}, headers=hdr(mint_token(MINE))).status_code == 200


def test_followups_needs_write_scope(client):
    """It marks events as prompted — a read-only share token must not swallow them."""
    url = f"/api/followups?guild_id={MINE}"
    assert client.get(url).status_code == 403
    assert client.get(url, headers=hdr(mint_token(MINE, scope=SCOPE_READ))).status_code == 403
    assert client.get(url, headers=hdr(mint_token(MINE))).status_code == 200


# ── capture: sync and async ─────────────────────────────────────────────────


def test_ingest_without_token_rejected(client):
    r = client.post(
        "/api/ingest",
        json={"urls": ["https://x.test/1"], "guild_id": MINE, "user_id": ME},
    )
    assert r.status_code == 403


def test_ingest_with_other_guilds_token_rejected(client):
    r = client.post(
        "/api/ingest",
        json={"urls": ["https://x.test/1"], "guild_id": THEIRS, "user_id": ME},
        headers=hdr(mint_token(MINE, user_id=ME)),
    )
    assert r.status_code == 403


def test_job_submit_requires_token(client):
    body = {"urls": ["https://x.test/1"], "guild_id": MINE, "user_id": ME}
    assert client.post("/api/jobs", json=body).status_code == 403
    assert client.post(
        "/api/jobs",
        json={**body, "guild_id": THEIRS},
        headers=hdr(mint_token(MINE, user_id=ME)),
    ).status_code == 403


def test_capture_user_identity_is_signed(client):
    """Changing user_id must invalidate the token instead of selecting a fresh counter."""
    body = {"urls": ["https://x.test/1"], "guild_id": MINE, "user_id": YOU}
    response = client.post(
        "/api/jobs", json=body, headers=hdr(mint_token(MINE, user_id=ME))
    )
    assert response.status_code == 403


def test_job_status_only_readable_by_its_tenant(client, monkeypatch):
    """A finished job carries that guild's new spots."""
    async def fake(urls, guild_id, on_stage):
        return {"added": 1, "events": [{"id": "secret-spot"}]}

    monkeypatch.setattr(admin, "_jobs", JobQueue(fake))
    resp = client.post(
        "/api/jobs",
        json={"urls": ["https://x.test/1"], "guild_id": MINE, "user_id": ME},
        headers=hdr(mint_token(MINE, user_id=ME)),
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    for _ in range(100):
        r = client.get(f"/api/jobs/{job_id}", headers=hdr(mint_token(MINE)))
        if r.json()["state"] == "done":
            break
        time.sleep(0.02)
    assert r.status_code == 200 and r.json()["state"] == "done"

    assert client.get(f"/api/jobs/{job_id}").status_code == 403
    assert client.get(f"/api/jobs/{job_id}", headers=hdr(mint_token(THEIRS))).status_code == 403


def _submit_capture(client, url: str, user_id: str = ME):
    return client.post(
        "/api/jobs",
        json={"urls": [url], "guild_id": MINE, "user_id": user_id},
        headers=hdr(mint_token(MINE, user_id=user_id)),
    )


def test_per_user_capture_limit_returns_retry_after(client, monkeypatch, caplog):
    monkeypatch.setattr(
        admin,
        "_capture_limits",
        CaptureRateLimiter(user_limit=1, user_window_s=600),
    )
    token = mint_token(MINE, user_id=ME)

    assert _submit_capture(client, "https://x.test/1").status_code == 202
    response = _submit_capture(client, "https://x.test/2")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "600"
    assert f"guild={MINE} user={ME} limit=user" in caplog.text
    assert token not in caplog.text


def test_per_server_capture_limit_returns_retry_after(client, monkeypatch):
    monkeypatch.setattr(
        admin,
        "_capture_limits",
        CaptureRateLimiter(server_limit=1, server_window_s=3600),
    )

    assert _submit_capture(client, "https://x.test/1", ME).status_code == 202
    response = _submit_capture(client, "https://x.test/2", YOU)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"
    assert response.json()["detail"] == "capture rate limit exceeded (server)"


def test_daily_server_capture_limit_returns_retry_after(client, monkeypatch):
    monkeypatch.setattr(
        admin,
        "_capture_limits",
        CaptureRateLimiter(daily_limit=1, daily_window_s=86400),
    )

    assert _submit_capture(client, "https://x.test/1", ME).status_code == 202
    response = _submit_capture(client, "https://x.test/2", YOU)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "86400"
    assert response.json()["detail"] == "capture rate limit exceeded (daily)"


def test_failed_job_list_only_readable_by_its_tenant(client):
    """The failed-job list names a guild's pasted links (feature #26)."""
    assert client.get(f"/api/jobs?guild_id={MINE}", headers=hdr(mint_token(MINE))).status_code == 200
    assert client.get(f"/api/jobs?guild_id={MINE}").status_code == 403
    assert client.get(f"/api/jobs?guild_id={MINE}", headers=hdr(mint_token(THEIRS))).status_code == 403
    assert client.get(f"/api/jobs?guild_id={THEIRS}", headers=hdr(mint_token(MINE))).status_code == 403


# ── share links ──────────────────────────────────────────────────────────────


def test_share_link_requires_token(client):
    """The IDOR that started this: ?guild_id=<any> used to render any catalog."""
    assert client.get(f"/share?guild_id={MINE}").status_code == 403


def test_share_link_with_valid_read_token(client):
    token = mint_token(MINE, scope=SCOPE_READ)
    r = client.get(f"/share?guild_id={MINE}&t={token}")
    assert r.status_code == 200 and token in r.text


def test_share_token_does_not_transfer_to_another_guild(client):
    token = mint_token(MINE, scope=SCOPE_READ)
    assert client.get(f"/share?guild_id={THEIRS}&t={token}").status_code == 403


def test_share_page_never_injects_non_hex(client):
    """The legacy page is open, and ?t= is echoed into a <script> — only hex survives."""
    r = client.get('/share?t=";alert(1)//')
    assert r.status_code == 200
    assert "alert(1)" not in r.text


# ── recommend service (:8003) ───────────────────────────────────────────────


class _StubService:
    def recommend(self, *a, **k):
        from src.ingestion.serving.recommender import RecommendationResult

        return RecommendationResult(recommendations=[], suppressed=True, reason="stub")

    def plan(self, *a, **k):
        from src.ingestion.serving.recommender import PlanResult

        return PlanResult(request={}, query="", recommendations=[])


@pytest.fixture
def rec_client(monkeypatch):
    monkeypatch.setenv(ENV_VAR, KEY)
    monkeypatch.setattr(serving_app, "get_service", lambda guild_id="": _StubService())
    return TestClient(serving_app.app)


@pytest.mark.parametrize(
    "path, body",
    [
        ("/recommend", {"channel_id": "c", "message": "tacos", "mode": "command"}),
        ("/plan", {"channel_id": "c", "transcript": "a: tacos?"}),
    ],
)
def test_recommend_service_is_tenant_scoped(rec_client, path, body):
    assert rec_client.post(path, json={**body, "guild_id": MINE}).status_code == 403
    assert rec_client.post(path, json={**body, "guild_id": THEIRS}, headers=hdr(mint_token(MINE))).status_code == 403
    assert rec_client.post(path, json={**body, "guild_id": MINE}, headers=hdr(mint_token(MINE))).status_code == 200
    assert rec_client.post(path, json=body).status_code == 200          # legacy "" catalog


# ── key handling ─────────────────────────────────────────────────────────────


def test_missing_signing_key_fails_closed(client, monkeypatch):
    """No key must mean no access — never a silent fallback to trusting callers."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    r = client.get(f"/api/events?guild_id={MINE}", headers=hdr("a" * 64))
    assert r.status_code == 503


def test_legacy_empty_tenant_still_open(client):
    """Documented carve-out: the local single-tenant web UI keeps working."""
    assert client.get("/api/events").status_code == 200


def test_bot_and_api_mint_identical_tokens(monkeypatch):
    """app/tenant_auth.py is a hand-kept mirror (the bot image can't import src/);
    if the payload format drifts, every real call fails. Pin them together."""
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv(ENV_VAR, KEY)
    spec = importlib.util.spec_from_file_location(
        "bot_tenant_auth", Path(__file__).parent / "app" / "tenant_auth.py"
    )
    bot_side = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot_side)

    for guild, user, scope in [(MINE, "", "rw"), (MINE, ME, "rw"), ("dm-7", "", "r")]:
        assert bot_side.mint_token(guild, scope=scope, user_id=user) == mint_token(guild, scope=scope, user_id=user)
    assert bot_side.tenant_headers("") == {}
    assert bot_side.tenant_headers(MINE, user_id=ME) == {"X-Tenant-Token": mint_token(MINE, user_id=ME)}
