"""RSVPs, "find a time" polls and the phone app routes (ADR-0008)."""

import json

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.config import IngestionSettings
from src.ingestion.serving import mobile_app, plans
from src.ingestion.serving.tenant_auth import ENV_VAR, SCOPE_GUEST, mint_token
from src.ingestion.sinks.chroma_sink import ChromaSink

GUILD = "333333333333333333"
RID, RID2 = "rid-aaaaaaaa", "rid-bbbbbbbb"


# ── pure rules ────────────────────────────────────────────────────────────────

def test_rsvp_sets_and_changes_one_answer_per_person():
    meta = plans.apply_rsvp({}, RID, "  Maya  ", "going")
    meta = plans.apply_rsvp(meta, RID2, "sam", "maybe")
    assert plans.rsvp_summary(meta) == {"going": ["Maya"], "maybe": ["sam"], "cant": []}
    meta = plans.apply_rsvp(meta, RID, "Maya", "cant")              # changed her mind
    assert plans.rsvp_summary(meta) == {"going": [], "maybe": ["sam"], "cant": ["Maya"]}


@pytest.mark.parametrize("rid, name, state, msg", [
    (RID, "Maya", "yes", "state must be"),
    (RID, "   ", "going", "name is required"),
    ("short", "Maya", "going", "rsvp_id"),
    ("bad id with spaces!", "Maya", "going", "rsvp_id"),
])
def test_rsvp_rejects_bad_input(rid, name, state, msg):
    with pytest.raises(plans.PlanError, match=msg):
        plans.apply_rsvp({}, rid, name, state)


def test_rsvp_names_are_trimmed_and_capped():
    meta = plans.apply_rsvp({}, RID, "x" * 100, "going")
    assert len(plans.rsvp_summary(meta)["going"][0]) == plans.MAX_NAME


def test_rsvps_are_capped_per_spot(monkeypatch):
    monkeypatch.setattr(plans, "MAX_RSVPS", 2)
    meta = plans.apply_rsvp(plans.apply_rsvp({}, "rid-00000001", "a", "going"), "rid-00000002", "b", "going")
    meta = plans.apply_rsvp(meta, "rid-00000001", "a", "maybe")    # changing an answer is still fine
    with pytest.raises(plans.PlanError, match="limit"):
        plans.apply_rsvp(meta, "rid-00000003", "c", "going")


def test_garbage_metadata_reads_as_empty():
    assert plans.rsvp_summary({"rsvps": "not json"}) == {"going": [], "maybe": [], "cant": []}
    assert plans.poll_summary({"poll": "[1, 2]"}) is None


def test_poll_counts_picks_and_finds_the_leader():
    meta = plans.open_poll({}, ["2026-11-06T19:00", "2026-11-07T12:00:00", "2026-11-07T19:00"])
    meta = plans.vote_poll(meta, RID, "Maya", [0, 2])
    meta = plans.vote_poll(meta, RID2, "Sam", [2])
    s = plans.poll_summary(meta)
    assert [x["count"] for x in s["slots"]] == [1, 0, 2] and s["leading"] == 2
    assert s["slots"][1]["at"] == "2026-11-07T12:00"               # normalized
    meta = plans.vote_poll(meta, RID2, "Sam", [0])                   # voting again replaces
    assert [x["count"] for x in plans.poll_summary(meta)["slots"]] == [2, 0, 1]


def test_poll_without_votes_has_no_leader():
    meta = plans.open_poll({}, ["2026-11-06T19:00", "2026-11-07T19:00"])
    assert plans.poll_summary(meta)["leading"] is None


@pytest.mark.parametrize("slots", [["2026-11-06T19:00"], ["2026-11-06T19:00"] * 2, ["soon", "later"],
                                   [f"2026-11-0{i}T19:00" for i in range(1, 8)]])
def test_poll_needs_two_to_five_real_times(slots):
    with pytest.raises(plans.PlanError):
        plans.open_poll({}, slots)


def test_poll_vote_rejects_missing_poll_and_bad_picks():
    with pytest.raises(plans.PlanError, match="no poll"):
        plans.vote_poll({}, RID, "Maya", [0])
    meta = plans.open_poll({}, ["2026-11-06T19:00", "2026-11-07T19:00"])
    with pytest.raises(plans.PlanError, match="pick a time"):
        plans.vote_poll(meta, RID, "Maya", [5])


# ── the API, against a real on-disk Chroma ────────────────────────────────────

def fake_embedder(texts):
    return [[float((len(t) + i) % 7) for i in range(8)] for t in texts]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, "1" * 64)
    sink = ChromaSink(IngestionSettings(chroma_path=str(tmp_path)), embedder=fake_embedder)
    monkeypatch.setattr(admin, "_sink_for", lambda guild_id="", **_: sink)
    with TestClient(admin.app) as c:
        member = {"X-Tenant-Token": mint_token(GUILD)}
        spot = c.post("/api/manual", headers=member,
                      json={"guild_id": GUILD, "venue": "Chef Tian", "theme": "Hunan dry pots"}).json()
        c.spot_id = spot.get("id") or spot.get("event_id")
        yield c


def test_rsvp_poll_and_invite_end_to_end(client):
    member = {"X-Tenant-Token": mint_token(GUILD)}
    guest = {"X-Tenant-Token": client.post("/api/invite", headers=member, json={"guild_id": GUILD}).json()["token"]}
    sid = client.spot_id

    r = client.post(f"/api/events/{sid}/rsvp", headers=guest,
                    json={"guild_id": GUILD, "rsvp_id": RID, "name": "Sam", "state": "going"})
    assert r.status_code == 200
    r = client.post(f"/api/events/{sid}/poll", headers=member,
                    json={"guild_id": GUILD, "slots": ["2026-11-06T19:00", "2026-11-07T12:00"]})
    assert r.status_code == 200
    r = client.post(f"/api/events/{sid}/poll/vote", headers=guest,
                    json={"guild_id": GUILD, "rsvp_id": RID, "name": "Sam", "picks": [1]})
    assert r.json()["poll"]["leading"] == 1

    ev = next(e for e in client.get(f"/api/events?guild_id={GUILD}", headers=guest).json()["events"] if e["id"] == sid)
    assert ev["rsvps"]["going"] == ["Sam"] and ev["poll"]["slots"][1]["names"] == ["Sam"]


def test_bad_rsvp_is_a_400_and_unknown_spot_a_404(client):
    guest = {"X-Tenant-Token": mint_token(GUILD, scope=SCOPE_GUEST)}
    r = client.post(f"/api/events/{client.spot_id}/rsvp", headers=guest,
                    json={"guild_id": GUILD, "rsvp_id": RID, "name": "Sam", "state": "sure"})
    assert r.status_code == 400
    r = client.post("/api/events/nope/rsvp", headers=guest,
                    json={"guild_id": GUILD, "rsvp_id": RID, "name": "Sam", "state": "going"})
    assert r.status_code == 404


def test_invite_needs_a_real_catalog(client):
    assert client.post("/api/invite", json={"guild_id": ""}).status_code == 400


# ── the phone app's routes ────────────────────────────────────────────────────

def test_app_page_manifest_worker_and_icons_are_served(client):
    page = client.get("/app")
    assert page.status_code == 200 and "<title>SpotBot</title>" in page.text
    manifest = client.get("/app/manifest.webmanifest")
    data = json.loads(manifest.text)
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert data["share_target"]["action"] == "/app/share" and data["start_url"] == "/app"
    assert client.get("/app/sw.js").headers["service-worker-allowed"] == "/app"
    for size in (192, 512):
        icon = client.get(f"/app/icon-{size}.png")
        assert icon.status_code == 200 and icon.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/app/icon-64.png").status_code == 404


def test_share_target_moves_the_link_into_the_fragment(client):
    r = client.get("/app/share", params={"text": "look https://www.instagram.com/reel/ABC/"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/app#shared=") and "instagram.com" not in r.headers["location"].split("#")[0]


def test_app_never_builds_html_from_data():
    """Names and venues come from other people; the page must only use textContent."""
    script = mobile_app.APP_HTML.split("<script>")[1]
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "document.write" not in script
