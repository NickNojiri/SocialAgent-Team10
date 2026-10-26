"""Adversarial tests: fuzzed webhook deliveries, the not-a-place list under random move/expire/
restore sequences and many threads, and model replies cut off or shaped wrong. Each one pins a
bug found in the 2026-10-26 audit or by these tests themselves. All offline and seeded."""

import hashlib
import hmac
import json
import random
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
import src.ingestion.serving.ig_webhook_app as hook
from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.summarizer import clean_title, describe_post
from src.ingestion.serving.gists import EXPIRY_DAYS, MAX_PER_SERVER, GistStore, gist_id
from src.ingestion.serving.guild_settings import GuildSettingsStore
from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token
from src.ingestion.sources.ig_webhook import (SharedPost, dm_key_url, is_post_permalink, scrub_event,
                                              shared_posts)

GUILD = "111111111111111111"
ME = "user-me"
MANUAL = "manual://discord"
SETTINGS = IngestionSettings(geocode_enabled=False)
REEL = "https://www.instagram.com/reel/DPxN0tZjV-2/"


# ── webhook deliveries: never raise, whatever the shape ────────────────────

def _junk(rng, depth=0):
    """A random JSON value, biased toward the keys a real delivery uses."""
    keys = ["object", "entry", "messaging", "message", "sender", "id", "attachments", "type",
            "payload", "url", "title", "caption", "text", "is_echo", "reel_video_id", "media_id"]
    pick = rng.randrange(9 if depth < 4 else 5)
    if pick == 0:
        return None
    if pick == 1:
        return rng.choice([True, False, 0, -1, 2 ** 70, 1.5])
    if pick == 2:
        return rng.choice(["", "instagram", "ig_reel", "share", REEL, "https://evil.test/x",
                           "  ", "‮", "x" * 300, "https://www.instagram.com/p/AbC/ trailing"])
    if pick in (3, 4):
        return rng.choice(["instagram", "page", "ig_reel", "share"])
    if pick in (5, 6):
        return {rng.choice(keys): _junk(rng, depth + 1) for _ in range(rng.randrange(1, 5))}
    return [_junk(rng, depth + 1) for _ in range(rng.randrange(0, 4))]


def _real(rng):
    message = {"mid": "m", "attachments": [{"type": "ig_reel", "payload": {
        "url": REEL, "title": "tacos", "reel_video_id": "18000000000000001"}}], "text": "see " + REEL}
    return {"object": "instagram", "entry": [{"id": "IG", "messaging": [
        {"sender": {"id": "1784"}, "recipient": {"id": "IG"}, "message": message}]}]}


def _mutate(rng, node):
    """The real shape with one random node swapped for junk: the likeliest surprise."""
    if isinstance(node, dict) and node and rng.random() < 0.8:
        key = rng.choice(list(node))
        return {**node, key: _mutate(rng, node[key]) if rng.random() < 0.7 else _junk(rng)}
    if isinstance(node, list) and node and rng.random() < 0.8:
        i = rng.randrange(len(node))
        return node[:i] + [_mutate(rng, node[i]) if rng.random() < 0.7 else _junk(rng)] + node[i + 1:]
    return _junk(rng)


@pytest.mark.parametrize("seed", range(4))
def test_random_and_mutated_deliveries_never_raise(seed):
    rng = random.Random(seed)
    for _ in range(400):
        payload = _mutate(rng, _real(rng)) if rng.random() < 0.7 else {"object": "instagram", "entry": _junk(rng)}
        posts = shared_posts(payload)
        scrub_event(payload)
        for post in posts:
            assert isinstance(post, SharedPost) and isinstance(post.capture_url, str)
            assert post.caption is None or isinstance(post.caption, str)
            assert (is_post_permalink(post.capture_url) or post.capture_url.startswith(dm_key_url("")[:-1])
                    or post.kind == "link")


def test_a_bare_string_sender_is_still_hashed_in_the_event_log():
    out = scrub_event({"entry": [{"messaging": [{"sender": "17841400000000001", "recipient": 42}]}]})
    event = out["entry"][0]["messaging"][0]
    assert "17841400000000001" not in json.dumps(out) and event["sender"].startswith("h_")
    assert event["recipient"].startswith("h_")


@pytest.fixture
def hook_client(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_APP_SECRET", "s")
    monkeypatch.setenv("IG_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("IG_SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(hook, "_queue_capture", lambda post: True)
    monkeypatch.setattr(hook, "_reply", lambda to, text: None)
    return TestClient(hook.app, raise_server_exceptions=False)


def _signed(client, body: bytes):
    sig = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": sig})


def test_signed_deliveries_of_any_shape_get_a_fast_200_never_a_500(hook_client):
    rng = random.Random(7)
    for _ in range(60):
        r = _signed(hook_client, json.dumps(_mutate(rng, _real(rng))).encode())
        assert r.status_code == 200, r.text


def test_json_nested_past_pythons_stack_is_a_400_not_a_500(hook_client):
    half = hook.MAX_BODY_BYTES // 2 - 1              # under the body limit, deeper than json can parse
    assert _signed(hook_client, b"[" * half + b"]" * half).status_code == 400


# ── the not-a-place list: moved spots are never lost ───────────────────────

class Collection:
    """Chroma stand-in holding several spots; `fail_adds` makes the next adds raise."""

    def __init__(self, rows):
        self.rows, self.fail_adds = dict(rows), 0

    def get(self, ids=None, include=None):
        hit = [i for i in (ids or self.rows) if i in self.rows]
        return {"ids": hit, "metadatas": [self.rows[i][0] for i in hit],
                "documents": [self.rows[i][1] for i in hit], "embeddings": [self.rows[i][2] for i in hit]}

    def delete(self, ids=None):
        for i in ids:
            self.rows.pop(i, None)

    def add(self, ids, metadatas, documents, embeddings=None):
        if self.fail_adds:
            self.fail_adds -= 1
            raise RuntimeError("chroma is down")
        self.rows[ids[0]] = (metadatas[0], documents[0], embeddings[0] if embeddings else None)


def _spot(name, url=MANUAL):
    return ({"venue_name": name, "category": "food", "votes": 1, "voters": '{"u1": "Ana"}',
             "source_url": url, "summary": f"{name} summary"}, f"{name} doc", [0.1, 0.2])


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    sink = type("Sink", (), {})()
    sink.collection = Collection({"m1": _spot("Taco Cart"), "m2": _spot("Boba Stand"), "r1": _spot("Reel Spot", REEL)})
    monkeypatch.setattr(admin, "_sink_for", lambda guild_id="", **_: sink)
    monkeypatch.setattr(admin, "_gists", GistStore(tmp_path / "gists"))
    monkeypatch.setattr(admin, "_guild_settings", GuildSettingsStore(tmp_path / "settings"))
    monkeypatch.setattr(admin, "_TAPS", tmp_path / "taps.jsonl")
    monkeypatch.setattr(admin, "describe_post", lambda *a, **k: None)
    with TestClient(admin.app, raise_server_exceptions=False) as client:
        yield client, sink.collection


def _hdr():
    return {"X-Tenant-Token": mint_token(GUILD, user_id=ME)}


def _move(client, eid):
    return client.post(f"/api/events/{eid}/not-a-place?guild_id={GUILD}", json={"user_id": ME}, headers=_hdr())


def _restore(client, gid):
    return client.post(f"/api/gists/{gid}/restore?guild_id={GUILD}", json={"user_id": ME}, headers=_hdr())


def test_two_manual_spots_moved_are_both_kept_and_both_come_back(api):
    client, col = api
    before = dict(col.rows)
    a, b = _move(client, "m1").json()["id"], _move(client, "m2").json()["id"]
    assert a != b and len(admin._gists.list(GUILD)) == 2        # they share "manual://discord"
    assert _restore(client, a).status_code == 200 and _restore(client, b).status_code == 200
    assert col.rows == before


def test_a_failed_restore_keeps_the_only_copy_and_can_be_retried(api):
    client, col = api
    gid = _move(client, "m1").json()["id"]
    col.fail_adds = 1
    assert _restore(client, gid).status_code == 500
    assert admin._gists.get(GUILD, gid)["spot"]["id"] == "m1"     # still there to retry
    assert _restore(client, gid).status_code == 200 and "m1" in col.rows
    assert admin._gists.get(GUILD, gid) is None


def test_the_same_reel_failing_again_doesnt_wipe_a_moved_spot(api):
    client, col = api
    gid = _move(client, "r1").json()["id"]
    admin._gists.add(GUILD, REEL, {"title": "Pasted Again"})       # a later paste of that reel
    admin._gists.add(GUILD, REEL, {"title": "Pasted Again"}, key=f"spot:r1")  # even under the same key
    assert _restore(client, gid).status_code == 200 and "r1" in col.rows


def test_moved_spots_outlive_expiry_and_the_cap(tmp_path):
    store = GistStore(tmp_path)
    store.add(GUILD, MANUAL, {"title": "Moved"}, spot={"id": "m1"}, key="spot:m1")
    for i in range(MAX_PER_SERVER + 20):
        store.add(GUILD, f"https://x.test/{i}", {"title": f"Plain {i}"})
    later = time.time() + (EXPIRY_DAYS + 5) * 86400
    rows = store.list(GUILD, now=later)
    assert [r["id"] for r in rows] == [gist_id("spot:m1")] and rows[0]["moved"]
    assert store.get(GUILD, gist_id("spot:m1"))["spot"] == {"id": "m1"}


@pytest.mark.parametrize("seed", range(6))
def test_random_move_paste_expire_restore_sequences_never_lose_a_spot(tmp_path, seed):
    """Model check: every spot moved and not yet restored can always be read back whole."""
    rng, store = random.Random(seed), GistStore(tmp_path / str(seed))
    urls = [MANUAL, REEL, "https://x.test/a", "https://x.test/b"]
    moved: dict[str, dict] = {}
    clock = time.time()
    for step in range(300):
        op = rng.random()
        if op < 0.3:
            eid = f"e{step}"
            spot = {"id": eid, "n": step}
            store.add(GUILD, rng.choice(urls), {"title": "t"}, spot=spot, key=f"spot:{eid}")
            moved[eid] = spot
        elif op < 0.6:
            store.add(GUILD, rng.choice(urls + [f"https://x.test/{step}"]), {"title": "pasted"})
        elif op < 0.75:
            clock += rng.choice([0, 86400, EXPIRY_DAYS * 86400])
            store.list(GUILD, expire=rng.random() < 0.8, now=clock)
        elif moved:
            eid = rng.choice(sorted(moved))
            assert store.pop(GUILD, gist_id(f"spot:{eid}"))["spot"] == moved.pop(eid)
        for eid, spot in moved.items():
            assert (store.get(GUILD, gist_id(f"spot:{eid}")) or {}).get("spot") == spot, (step, eid)


def test_many_threads_adding_at_once_leave_a_whole_file(tmp_path):
    store, errors = GistStore(tmp_path), []

    def work(t):
        try:
            for i in range(40):
                store.add(GUILD, f"https://x.test/{t}/{i}", {"title": f"{t}-{i}"})
                if i % 7 == 0:
                    store.list(GUILD)
        except Exception as exc:            # noqa: BLE001 — any failure fails the test below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert len(json.loads(store.path_for(GUILD).read_text(encoding="utf-8"))) == min(320, MAX_PER_SERVER)


# ── model replies cut off or shaped wrong ──────────────────────────────────

class _Ollama:
    def __init__(self, text):
        self.text = text

    def __call__(self, url, json=None, timeout=None):
        return httpx.Response(200, json={"response": self.text}, request=httpx.Request("POST", url))


FULL = json.dumps({"title": "Grandma Teaches Her Dumpling Fold", "kind": "recipe",
                   "summary": "She shows the pleat \"three times\" and laughs when it tears."})


def test_a_reply_cut_off_mid_summary_keeps_what_was_said_and_no_json(monkeypatch):
    monkeypatch.setattr(httpx, "post", _Ollama(FULL[:-25]))
    gist = describe_post("grandma's dumplings, a long caption", "she folds them", None, "nai", SETTINGS)
    assert gist["title"] == "Grandma Teaches Her Dumpling Fold" and gist["kind"] == "recipe"
    assert gist["summary"].startswith('She shows the pleat "three times"') and "{" not in gist["summary"]


@pytest.mark.parametrize("cut", range(1, len(FULL)))
def test_every_cut_point_gives_none_or_a_clean_card(monkeypatch, cut):
    monkeypatch.setattr(httpx, "post", _Ollama(FULL[:cut]))
    gist = describe_post("grandma's dumplings, a long caption", "she folds them", None, "nai", SETTINGS)
    if gist is not None:
        shown = gist["title"] + gist["summary"]
        assert '"title"' not in shown and '"summary"' not in shown and not shown.lstrip().startswith("{")
        assert gist["kind"] in ("recipe", "story")


@pytest.mark.parametrize("reply", [
    "[1, 2, 3]", '"just a string"', "42", "null", '{"summary": {"nested": true}}',
    '{"title": ["a", "list"], "summary": "Fine summary here."}', "{}", "   ",
])
def test_json_of_the_wrong_shape_never_reaches_a_card_as_a_repr(monkeypatch, reply):
    monkeypatch.setattr(httpx, "post", _Ollama(reply))
    gist = describe_post("a long enough caption about nothing", "speech", None, "x", SETTINGS)
    if gist is not None:
        for value in gist.values():
            assert isinstance(value, str) and "{" not in value and "[" not in value


@pytest.mark.parametrize("title", [
    None, 42, ["Two", "Words"], {"a": 1}, "word " + "x" * 400, "​​", "Tacos‮evil",
    "\n\n", "Line one\nLine two here",
])
def test_titles_of_any_shape_are_shown_safe(title):
    out = clean_title(title, "caption", "acct")
    assert isinstance(out, str) and "\n" not in out and len(out) <= 100
    assert out == "@acct's reel" or isinstance(title, str)
