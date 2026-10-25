"""Instagram webhook capture (ADR-0007): Meta's signature and handshake, parsing shared reels,
the shared-post store the capture chain reads, and the public webhook app. All offline: the
admin app and Meta's Graph API are stubbed.

The webhook URL is public, so the signature tests are the security boundary (THREAT_MODEL T10).
"""

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.ig_webhook_app as hook
from src.ingestion.serving.tenant_auth import ENV_VAR, verify_token
from src.ingestion.sources.ig_webhook import (
    FirstOf,
    SharedPost,
    SharedPostStore,
    dm_key_url,
    handshake,
    is_post_permalink,
    scrub_event,
    shared_posts,
    verify_signature,
)

SECRET = "test-app-secret"
VERIFY = "test-verify-token"
SENDER = "17841400000000001"
REEL = "https://www.instagram.com/reel/DPxN0tZjV-2/"
CDN = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=123&signature=abc"


def _delivery(*messages, sender=SENDER):
    return {"object": "instagram", "entry": [{"id": "IGID", "time": 1, "messaging": [
        {"sender": {"id": sender}, "recipient": {"id": "IGID"}, "timestamp": 1, "message": m} for m in messages]}]}


def _reel(url=REEL, title="Best tacos in LA 📍Leo's Tacos", vid="18000000000000001", kind="ig_reel"):
    return {"mid": "m1", "attachments": [{"type": kind, "payload": {"url": url, "title": title, "reel_video_id": vid}}]}


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── signature and handshake ─────────────────────────────────────────────────


def test_signature_accepts_the_right_hmac_only():
    body = b'{"object":"instagram"}'
    assert verify_signature(SECRET, body, _sign(body))
    assert not verify_signature(SECRET, body, _sign(body, "other-secret"))
    assert not verify_signature(SECRET, body + b" ", _sign(body))       # body changed after signing
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature(SECRET, body, "sha1=" + "0" * 40)        # old header style
    assert not verify_signature("", body, _sign(body, ""))               # no secret configured: refuse


def test_handshake_echoes_the_challenge_only_for_our_token():
    assert handshake("subscribe", VERIFY, "12345", VERIFY) == "12345"
    assert handshake("subscribe", "wrong", "12345", VERIFY) is None
    assert handshake("unsubscribe", VERIFY, "12345", VERIFY) is None
    assert handshake("subscribe", VERIFY, "12345", "") is None           # not configured


# ── parsing ─────────────────────────────────────────────────────────────────


def test_shared_reel_with_a_permalink_is_captured_by_its_page():
    [post] = shared_posts(_delivery(_reel()))
    assert post.capture_url == REEL and post.kind == "ig_reel" and post.sender_id == SENDER
    assert post.caption.startswith("Best tacos") and post.media_url is None


def test_shared_reel_with_only_a_video_link_gets_a_synthetic_key():
    [post] = shared_posts(_delivery(_reel(url=CDN)))
    assert post.capture_url == dm_key_url("18000000000000001")
    assert post.media_url == CDN and post.has_payload


def test_post_shares_and_pasted_links_are_found_but_other_messages_are_not():
    delivery = _delivery(
        _reel(url="https://www.instagram.com/p/ABCDE12345/", kind="ig_post", vid=None) | {"mid": "m2"},
        {"mid": "m3", "text": "try this https://www.instagram.com/reel/XYZ12345/?igsh=abc, and https://evil.example/x"},
        {"mid": "m4", "attachments": [{"type": "image", "payload": {"url": "https://cdn.example/x.jpg"}}]},
        {"mid": "m5", "is_echo": True, "text": "https://www.instagram.com/reel/ECHO12345/"},
        {"mid": "m6", "is_unsupported": True},
    )
    urls = [p.capture_url for p in shared_posts(delivery)]
    assert urls == ["https://www.instagram.com/p/ABCDE12345/", "https://www.instagram.com/reel/XYZ12345/?igsh=abc"]


def test_the_same_reel_twice_in_one_delivery_is_one_post():
    assert len(shared_posts(_delivery(_reel(), _reel() | {"mid": "m2"}))) == 1


def test_garbage_deliveries_parse_to_nothing():
    assert shared_posts({}) == []
    assert shared_posts({"object": "whatsapp_business_account", "entry": []}) == []
    assert shared_posts({"object": "instagram", "entry": [{"messaging": [{"message": "x"}]}]}) == []
    assert shared_posts(_delivery({"attachments": [{"type": "ig_reel", "payload": {}}]})) == []


def test_permalink_check():
    assert is_post_permalink(REEL)
    assert is_post_permalink("https://instagram.com/p/ABCDE12345")
    assert not is_post_permalink(CDN)
    assert not is_post_permalink("https://www.instagram.com/someprofile/")
    assert not is_post_permalink("https://instagram.com.evil.example/reel/ABCDE12345/")


def test_scrubbed_event_keeps_no_raw_ids():
    text = json.dumps(scrub_event(_delivery(_reel())))
    assert SENDER not in text and '"h_' in text and "Best tacos" in text


# ── the store the capture chain reads ───────────────────────────────────────


def test_store_serves_a_video_only_share_to_the_capture_chain(tmp_path):
    store = SharedPostStore(tmp_path)
    [post] = shared_posts(_delivery(_reel(url=CDN)))
    assert store.put(post) is not None
    raw = store.fetch_url(post.capture_url)
    assert raw.caption.startswith("Best tacos") and raw.video_url == CDN
    assert raw.platform == "instagram" and raw.capture_path == "dm_share"
    assert store.fetch_url(REEL) is None                               # never DM'd → not served


def test_store_skips_permalink_shares_so_the_public_page_is_used(tmp_path):
    store = SharedPostStore(tmp_path)
    [post] = shared_posts(_delivery(_reel()))
    assert store.put(post) is None and store.fetch_url(REEL) is None


def test_store_prunes_old_files(tmp_path):
    store = SharedPostStore(tmp_path, max_age_s=-1)                    # everything counts as old
    first = SharedPost(dm_key_url("1"), SENDER, "ig_reel", "a", "1", CDN)
    store.put(first)
    store.put(SharedPost(dm_key_url("2"), SENDER, "ig_reel", "b", "2", CDN))
    assert store.fetch_url(first.capture_url) is None


def test_first_of_falls_through_and_only_account_sources_count_misses(tmp_path):
    store = SharedPostStore(tmp_path)

    class Account:
        def fetch_url(self, url):
            return "from-account"

    assert FirstOf(store, None).counts_misses is False
    assert FirstOf(store, Account()).counts_misses is True
    assert FirstOf(store, Account()).fetch_url(REEL) == "from-account"


# ── the public app ──────────────────────────────────────────────────────────


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_APP_SECRET", SECRET)
    monkeypatch.setenv("IG_WEBHOOK_VERIFY_TOKEN", VERIFY)
    monkeypatch.setenv("IG_SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("IG_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IG_WEBHOOK_GUILD_ID", raising=False)
    monkeypatch.setattr(hook, "_limits", hook._SenderLimit(20, 3600))
    queued, replies = [], []
    monkeypatch.setattr(hook, "_queue_capture", lambda post: queued.append(post) or True)
    monkeypatch.setattr(hook, "_reply", lambda to, text: replies.append((to, text)))
    return {"queued": queued, "replies": replies, "dir": tmp_path}


def _post(client, payload, secret=SECRET, sign=True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(body, secret)
    return client.post("/webhooks/instagram", content=body, headers=headers)


def test_app_exposes_only_the_webhook_and_health():
    paths = {r.path for r in hook.app.routes}
    assert paths == {"/webhooks/instagram", "/health"}


def test_app_handshake(app_env):
    c = TestClient(hook.app)
    ok = c.get("/webhooks/instagram", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "987"})
    assert ok.status_code == 200 and ok.text == "987"
    assert c.get("/webhooks/instagram", params={"hub.mode": "subscribe", "hub.verify_token": "no",
                                                "hub.challenge": "987"}).status_code == 403


def test_app_queues_a_signed_reel_and_writes_the_event_log(app_env):
    r = _post(TestClient(hook.app), _delivery(_reel(url=CDN)))
    assert r.status_code == 200 and r.text == "EVENT_RECEIVED"
    [post] = app_env["queued"]
    assert post.capture_url == dm_key_url("18000000000000001")
    assert SharedPostStore(app_env["dir"] / "shared").fetch_url(post.capture_url) is not None
    assert app_env["replies"] == [(SENDER, "Got it! Adding this spot to SpotBot.")]
    assert SENDER not in (app_env["dir"] / "events.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("secret,sign", [("wrong-secret", True), (SECRET, False)])
def test_app_refuses_unsigned_or_wrongly_signed_posts(app_env, secret, sign):
    r = _post(TestClient(hook.app), _delivery(_reel()), secret=secret, sign=sign)
    assert r.status_code == 403 and app_env["queued"] == []
    assert not (app_env["dir"] / "events.jsonl").exists()              # refused before parsing


def test_app_refuses_oversized_bodies(app_env):
    big = _delivery({"mid": "m", "text": "x" * (hook.MAX_BODY_BYTES + 10)})
    assert _post(TestClient(hook.app), big).status_code == 413 and app_env["queued"] == []


def test_app_unconfigured_refuses_deliveries(app_env, monkeypatch):
    monkeypatch.delenv("IG_APP_SECRET")
    assert _post(TestClient(hook.app), _delivery(_reel())).status_code == 503


def test_sender_limit_caps_captures(app_env, monkeypatch):
    monkeypatch.setattr(hook, "_limits", hook._SenderLimit(2, 3600))
    c = TestClient(hook.app)
    for i in range(3):
        _post(c, _delivery(_reel(url=f"https://www.instagram.com/reel/REEL{i}xxxx/")))
    assert len(app_env["queued"]) == 2
    assert "a lot of spots" in app_env["replies"][-1][1]


def test_queue_capture_signs_a_server_catalog_request(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    monkeypatch.setenv("IG_WEBHOOK_GUILD_ID", "111111111111111111")
    sent = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json, headers):
            sent.update(url=url, json=json, headers=headers)

            class R:
                status_code = 202

                @staticmethod
                def json():
                    return {"job_id": "j1"}

            return R()

    monkeypatch.setattr(hook, "_http_client", lambda **kw: FakeClient())
    post = SharedPost(REEL, SENDER, "ig_reel")
    assert hook._queue_capture(post) is True
    assert sent["url"].endswith("/api/jobs") and sent["json"]["urls"] == [REEL]
    user = sent["json"]["user_id"]
    assert user.startswith("ig:") and SENDER not in user                 # raw ids stay out of the catalog
    assert verify_token(sent["headers"]["X-Tenant-Token"], "111111111111111111", user_id=user)


def test_admin_capture_chain_reads_the_store_first():
    import src.ingestion.serving.admin as admin

    assert isinstance(admin._capture_source, FirstOf)
    assert isinstance(admin._capture_source.sources[0], SharedPostStore)
