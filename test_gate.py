"""The gatekeeper (serving/gate.py): only the phone app and token-checked calls get through."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src.ingestion.serving import gate

TOK = {"X-Tenant-Token": "abc123"}
JOB = "0123456789ab"


@pytest.fixture
def seen(monkeypatch):
    """The gate with a fake admin service that records what reached it."""
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"x-internal": "secret", "content-type": "application/json"})

    monkeypatch.setattr(gate, "_client", httpx.AsyncClient(base_url="http://admin.test", transport=httpx.MockTransport(upstream)))
    return SimpleNamespace(calls=calls, client=TestClient(gate.app))


@pytest.mark.parametrize("method, path, kw", [
    ("get", "/app", {}),
    ("get", "/app/sw.js", {}),
    ("get", "/app/icon-192.png", {}),
    ("get", "/app/share?url=https://www.instagram.com/reel/x/", {}),
    ("get", "/share?guild_id=1&t=abc", {}),
    ("get", "/api/events?guild_id=1", {"headers": TOK}),
    ("get", "/api/import?guild_id=1", {"headers": TOK}),
    ("get", f"/api/jobs/{JOB}", {"headers": TOK}),
    ("post", "/api/jobs", {"headers": TOK, "json": {"guild_id": "1", "user_id": "2", "urls": ["https://x.test/"]}}),
    ("post", "/api/import", {"headers": TOK, "json": {"guild_id": "1", "user_id": "2", "urls": []}}),
    ("post", "/api/invite", {"headers": TOK, "json": {"guild_id": "1", "user_id": "2"}}),
    ("post", "/api/events/abc-1/rsvp", {"headers": TOK, "json": {"guild_id": "1"}}),
    ("post", "/api/events/abc-1/poll/vote", {"headers": TOK, "json": {"guild_id": "1"}}),
    ("post", "/api/events/abc-1/vote?guild_id=1", {"headers": TOK, "json": {"delta": 1}}),
    ("post", "/api/events/abc-1/not-a-place?guild_id=1", {"headers": TOK, "json": {"user_id": "2"}}),
])
def test_the_phone_app_and_its_calls_get_through(seen, method, path, kw):
    r = getattr(seen.client, method)(path, **kw)
    assert r.status_code == 200 and len(seen.calls) == 1
    assert seen.calls[0].url.path == path.split("?")[0]
    assert "x-internal" not in r.headers                         # only safe headers come back
    assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("method, path, kw", [
    ("get", "/", {}),                                            # the admin UI (open legacy catalog)
    ("get", "/dash", {}),
    ("get", "/api/stats", {}),
    ("get", "/health", {}),
    ("get", "/docs", {}),
    ("get", "/api/events", {"headers": TOK}),                    # no server named: the legacy catalog
    ("get", "/api/events?guild_id=", {"headers": TOK}),
    ("get", "/api/events?guild_id=1", {}),                       # no token
    ("post", "/api/jobs", {"headers": TOK, "json": {"guild_id": "", "urls": ["https://x.test/"]}}),
    ("post", "/api/jobs", {"headers": TOK, "content": b"not json"}),
    ("get", "/api/jobs?guild_id=1&state=failed", {"headers": TOK}),
    ("get", "/api/jobs/not-a-job-id", {"headers": TOK}),
    ("post", "/api/forget", {"headers": TOK, "json": {"guild_id": "1", "confirm": "1"}}),
    ("delete", "/api/events/abc?guild_id=1", {"headers": TOK}),
    ("put", "/api/settings", {"headers": TOK, "json": {"guild_id": "1"}}),
    ("post", "/api/manual", {"headers": TOK, "json": {"guild_id": "1"}}),
    ("post", "/api/events/abc/edit", {"headers": TOK, "json": {"guild_id": "1"}}),
    ("get", "/share?guild_id=1", {}),                            # a share link always carries its token
    ("post", "/app", {}),
    ("get", "/app/../dash", {}),
    ("get", "/app/%2e%2e/dash", {}),
])
def test_everything_else_never_reaches_the_admin_service(seen, method, path, kw):
    r = getattr(seen.client, method)(path, **kw)
    assert r.status_code == 404 and seen.calls == []


def test_big_bodies_are_refused(seen):
    big = json.dumps({"guild_id": "1", "urls": ["https://www.instagram.com/reel/AAAAA/"] * 20000})
    r = seen.client.post("/api/import", headers={**TOK, "content-type": "application/json"}, content=big)
    assert r.status_code == 413 and seen.calls == []


def test_offline_admin_is_a_friendly_502(monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(gate, "_client", httpx.AsyncClient(base_url="http://admin.test", transport=httpx.MockTransport(down)))
    r = TestClient(gate.app).get("/app")
    assert r.status_code == 502 and "offline" in r.text


def test_every_rule_names_a_real_admin_route():
    """A rule for a route that no longer exists is dead weight; one for a renamed
    route would silently break the app. Keep the list in step with admin.py."""
    from src.ingestion.serving import admin
    routes = {(m, r.path) for r in admin.app.routes for m in getattr(r, "methods", set())}
    samples = {
        "/app/icon-(?:192|512)\\.png": "/app/icon-{size}.png",
        "/api/jobs/[0-9a-f]{12}": "/api/jobs/{job_id}",
    }
    for method, pattern, _ in gate.RULES:
        p = pattern.pattern.replace("\\.", ".")
        if p.startswith(gate._EVENT):
            for tail in p[len(gate._EVENT):].removeprefix("/(?:").removesuffix(")").split("|"):
                assert (method, "/api/events/{event_id}/" + tail) in routes, tail
            continue
        assert (method, samples.get(pattern.pattern, p)) in routes, p
