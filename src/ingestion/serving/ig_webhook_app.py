"""The one public piece of SpotBot: Meta's Instagram webhook (ADR-0007).

    uvicorn src.ingestion.serving.ig_webhook_app:app --host 127.0.0.1 --port 8011

A tunnel (scripts/run_instagram.ps1) or the staging proxy exposes ONLY this app, never the
admin app on :8010, whose legacy "" catalog is unauthenticated by design (THREAT_MODEL T8).
Routes, and nothing else (no /docs, no OpenAPI page):

  GET  /webhooks/instagram   Meta's subscription handshake (hub.verify_token → hub.challenge)
  POST /webhooks/instagram   message deliveries: signature-checked, then each shared reel is
                             queued as a capture job on the admin app over localhost
  GET  /health               which settings are present (booleans only)

Settings come from the environment (.env via the launch script), never from the request:
IG_APP_SECRET (signs Meta's POSTs; for Instagram Login it's the *Instagram* app secret),
IG_WEBHOOK_VERIFY_TOKEN (any random string, also typed into the Meta dashboard),
IG_ACCESS_TOKEN (optional: lets SpotBot reply "got it"), IG_WEBHOOK_GUILD_ID (which catalog
DM'd reels land in; "" = the local one), ADMIN_URL (default http://127.0.0.1:8010).
"""

import hashlib
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from src.ingestion.serving.tenant_auth import mint_token
from src.ingestion.sources.ig_webhook import (
    SharedPost,
    SharedPostStore,
    handshake,
    scrub_event,
    shared_posts,
    verify_signature,
)

log = logging.getLogger("ingestion.ig_webhook_app")

MAX_BODY_BYTES = 256 * 1024        # Meta's message deliveries are a few KB
_EVENT_LOG_MAX_BYTES = 5 * 1024 * 1024

app = FastAPI(title="SpotBot Instagram webhook", docs_url=None, redoc_url=None, openapi_url=None)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


class _SenderLimit:
    """At most `limit` captures per sender per `window_s` (a tester pasting 50 reels at once
    shouldn't fill the capture queue). In memory: a restart forgets, which is fine here."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self._seen: dict[str, deque] = defaultdict(deque)

    def allow(self, sender: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        q = self._seen[sender]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


_limits = _SenderLimit(int(_env("IG_WEBHOOK_SENDER_LIMIT", "20")), float(_env("IG_WEBHOOK_SENDER_WINDOW_S", "3600")))


def _store() -> SharedPostStore:
    return SharedPostStore(Path(_env("IG_SHARED_DIR", "data/ig_webhook/shared")))


def _sender_key(sender: str) -> str:
    """The id the catalog and its rate limits see: a hash, so raw Instagram ids stay out of it."""
    return "ig:" + hashlib.sha256(sender.encode("utf-8")).hexdigest()[:16]


def _http_client(**kw) -> httpx.Client:
    """An httpx client that trusts the OS certificate store (truststore), so outbound calls work
    on networks that intercept TLS without turning verification off."""
    try:
        import ssl

        import truststore

        kw.setdefault("verify", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    except ImportError:
        pass
    return httpx.Client(timeout=20, **kw)


# ── routes ───────────────────────────────────────────────────────────────────


@app.get("/webhooks/instagram")
def subscribe(request: Request):
    q = request.query_params
    challenge = handshake(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"),
                          _env("IG_WEBHOOK_VERIFY_TOKEN"))
    if challenge is None:
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(challenge)


@app.post("/webhooks/instagram")
async def deliver(request: Request, background: BackgroundTasks):
    secret = _env("IG_APP_SECRET")
    if not secret:
        return PlainTextResponse("not configured", status_code=503)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return PlainTextResponse("too large", status_code=413)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return PlainTextResponse("too large", status_code=413)
    if not verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
        log.warning("[ig-webhook] refused a delivery with a missing or wrong signature")
        return PlainTextResponse("forbidden", status_code=403)
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):      # RecursionError: nesting deeper than Python's stack
        return PlainTextResponse("bad json", status_code=400)
    try:
        _log_event(payload)
        posts = shared_posts(payload)
    except Exception as exc:
        # Signed but shaped in a way we don't read: still 200, or Meta redelivers it forever.
        log.warning(f"[ig-webhook] couldn't read a delivery: {type(exc).__name__}")
        posts = []
    if posts:
        background.add_task(_handle, posts)
    # 200 fast, whatever happens next: a non-200 makes Meta redeliver (a duplicate capture).
    return PlainTextResponse("EVENT_RECEIVED")


@app.get("/health")
def health():
    return JSONResponse({
        "ok": True,
        "app_secret": bool(_env("IG_APP_SECRET")),
        "verify_token": bool(_env("IG_WEBHOOK_VERIFY_TOKEN")),
        "replies": bool(_env("IG_ACCESS_TOKEN")),
    })


# ── work done after the 200 ──────────────────────────────────────────────────


def _handle(posts: list[SharedPost]) -> None:
    store = _store()
    for post in posts:
        if not _limits.allow(post.sender_id):
            log.info("[ig-webhook] sender over the capture limit; skipped one post")
            _reply(post.sender_id, "That's a lot of spots at once! Send more in a little while.")
            continue
        store.put(post)
        queued = _queue_capture(post)
        _reply(post.sender_id, "Got it! Adding this spot to SpotBot." if queued
               else "SpotBot couldn't save that one right now. Try sending it again in a bit.")


def _queue_capture(post: SharedPost) -> bool:
    guild = _env("IG_WEBHOOK_GUILD_ID")
    user = _sender_key(post.sender_id)
    headers = {"X-Tenant-Token": mint_token(guild, user_id=user)} if guild else {}
    try:
        with _http_client() as client:
            resp = client.post(f"{_env('ADMIN_URL', 'http://127.0.0.1:8010')}/api/jobs",
                               json={"urls": [post.capture_url], "guild_id": guild, "user_id": user},
                               headers=headers)
    except httpx.HTTPError as exc:
        log.warning(f"[ig-webhook] admin app unreachable: {type(exc).__name__}")
        return False
    if resp.status_code != 202:
        log.warning(f"[ig-webhook] admin app refused the capture: HTTP {resp.status_code}")
        return False
    log.info(f"[ig-webhook] queued {post.kind} capture job {resp.json().get('job_id')}")
    return True


def _reply(recipient: str, text: str) -> None:
    """Best-effort DM back to the sender (inside Meta's 24-hour messaging window). Off when
    IG_ACCESS_TOKEN isn't set; a failure is logged without the token or the reply text."""
    token = _env("IG_ACCESS_TOKEN")
    if not token:
        return
    version = _env("IG_GRAPH_VERSION", "v25.0")
    try:
        with _http_client() as client:
            resp = client.post(f"https://graph.instagram.com/{version}/me/messages",
                               json={"recipient": {"id": recipient}, "message": {"text": text}},
                               headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 300:
            log.warning(f"[ig-webhook] reply refused: HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        log.warning(f"[ig-webhook] reply failed: {type(exc).__name__}")


def _log_event(payload: dict) -> None:
    """Keep each delivery (ids hashed) under data/, to turn the first real one into the test
    fixture. Stops growing at 5 MB."""
    path = Path(_env("IG_EVENT_LOG", "data/ig_webhook/events.jsonl"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _EVENT_LOG_MAX_BYTES:
            return
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(scrub_event(payload), ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning(f"[ig-webhook] event log write failed: {exc}")
