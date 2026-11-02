"""The gatekeeper: the only door from a tunnel to the admin service.

A tunnel (Cloudflare quick tunnel, Tailscale Funnel) puts whatever it points at on the
public internet. It points here, never at the admin service. This app forwards only
the phone app (/app), the read-only share page, and the token-checked calls those
pages make, each for a named server. Everything else is a 404 that never reaches the
admin service: the operations dashboard, /api/stats, the admin UI, the open legacy
catalog ("" guild) and every operator write. Same rule as deploy/Caddyfile for staging.

    uvicorn src.ingestion.serving.gate:app --host 127.0.0.1 --port 8080
    GATE_UPSTREAM=http://127.0.0.1:8010   (where the admin service listens)
"""
from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

UPSTREAM = os.getenv("GATE_UPSTREAM", "http://127.0.0.1:8010").rstrip("/")
MAX_BODY = 512 * 1024          # an import of 2,000 links is about 100 KB

PAGE, SHARE, API, JOB = "page", "share", "api", "job"
_EVENT = r"/api/events/[A-Za-z0-9_-]{1,128}"
RULES: list[tuple[str, re.Pattern, str]] = [
    (m, re.compile(p), kind) for m, p, kind in [
        ("GET", r"/app", PAGE),
        ("GET", r"/app/manifest\.webmanifest", PAGE),
        ("GET", r"/app/sw\.js", PAGE),
        ("GET", r"/app/icon-(?:192|512)\.png", PAGE),
        ("GET", r"/app/share", PAGE),
        ("GET", r"/share", SHARE),
        ("GET", r"/api/events", API),
        ("GET", r"/api/import", API),
        ("POST", r"/api/import", API),
        ("POST", r"/api/jobs", API),
        ("GET", r"/api/jobs/[0-9a-f]{12}", JOB),
        ("POST", r"/api/invite", API),
        ("POST", _EVENT + r"/(?:rsvp|poll|poll/vote|vote|not-a-place)", API),
    ]
]
_PASS_REQ = ("content-type", "accept", "x-tenant-token")
_PASS_RESP = ("content-type", "location", "cache-control", "service-worker-allowed", "retry-after")
_SECURITY = {
    # Tokens ride in share-link query strings: never hand them on as a Referer.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def _lifespan(_app):
    yield
    if _client is not None:
        await _client.aclose()


app = FastAPI(title="SpotBot gatekeeper", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=UPSTREAM, timeout=60.0)
    return _client


def _rule(method: str, path: str) -> str | None:
    for m, pattern, kind in RULES:
        if m == method and pattern.fullmatch(path):
            return kind
    return None


def _guild(request: Request, body: bytes) -> str:
    gid = request.query_params.get("guild_id", "")
    if not gid and body:
        try:
            data = json.loads(body)
        except ValueError:
            return ""
        if isinstance(data, dict) and isinstance(data.get("guild_id"), str):
            gid = data["guild_id"]
    return gid.strip()


def allowed(request: Request, body: bytes) -> bool:
    """Whether this request may reach the admin service at all."""
    kind = _rule(request.method, request.url.path)
    if kind is None:
        return False
    if kind == PAGE:
        return True
    if kind == SHARE:
        q = request.query_params
        return bool(q.get("guild_id", "").strip() and q.get("t", "").strip())
    if not request.headers.get("x-tenant-token", "").strip():
        return False
    # A job id is 12 random hex characters; the admin app checks its tenant.
    return kind == JOB or bool(_guild(request, body))


def _not_found() -> Response:
    return Response("Not found", status_code=404, media_type="text/plain", headers=_SECURITY)


@app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def forward(request: Request, rest: str = "") -> Response:
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return Response("Too large", status_code=413, media_type="text/plain", headers=_SECURITY)
    body = await request.body()
    if len(body) > MAX_BODY:
        return Response("Too large", status_code=413, media_type="text/plain", headers=_SECURITY)
    if not allowed(request, body):
        return _not_found()
    headers = {k: v for k, v in request.headers.items() if k.lower() in _PASS_REQ}
    try:
        upstream = await client().request(
            request.method, request.url.path, params=list(request.query_params.multi_items()),
            content=body or None, headers=headers,
        )
    except httpx.HTTPError:
        return Response("SpotBot is starting or offline. Try again in a minute.", status_code=502,
                        media_type="text/plain", headers=_SECURITY)
    out = {k: v for k, v in upstream.headers.items() if k.lower() in _PASS_RESP}
    return Response(upstream.content, status_code=upstream.status_code, headers={**out, **_SECURITY})
