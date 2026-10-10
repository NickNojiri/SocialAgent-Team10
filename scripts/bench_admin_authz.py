"""Measure the catalog API's cross-tenant rejection rate.

    python scripts/bench_admin_authz.py

Drives the real FastAPI apps (admin :8010 and recommend :8003, storage stubbed)
with forged requests against every tenant-scoped endpoint and reports what
fraction are rejected. The negative control matters as much as the count: a gate
that rejects everything scores 100% and is useless, so authentic requests must
still be ACCEPTED. docs/THREAT_MODEL.md T2.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("SPOTBOT_SIGNING_KEY", "0" * 64)

from fastapi.testclient import TestClient  # noqa: E402

import src.ingestion.serving.admin as admin  # noqa: E402
import src.ingestion.serving.app as serving_app  # noqa: E402
from src.ingestion.serving.feedback import FeedbackStore  # noqa: E402
from src.ingestion.serving.guild_settings import GuildSettingsStore  # noqa: E402
from src.ingestion.serving.jobs import JobQueue  # noqa: E402
from src.ingestion.serving.time_to_card import TimeToCardLog  # noqa: E402
from src.ingestion.serving.tenant_auth import SCOPE_READ, mint_token  # noqa: E402
from test_admin_authz import StubSink, _StubService  # noqa: E402

MINE = "111111111111111111"
THEIRS = "222222222222222222"
ME, YOU = "user-me", "user-you"
EVENT = "abc123"


async def _fake_capture(urls, guild_id, on_stage):
    return {"added": 1, "events": [{"id": "new-spot"}]}


admin._sink_for = lambda guild_id="", **_: StubSink()
admin._purge_catalog = lambda guild_id: {"spots": 0}       # never the real data/
admin._jobs = JobQueue(_fake_capture)
admin._guild_settings = GuildSettingsStore(Path(tempfile.mkdtemp(prefix="bench_settings_")))
admin._feedback = FeedbackStore(Path(tempfile.mkdtemp(prefix="bench_feedback_")))
admin._geocode_city = lambda city: None                      # never the network
admin._time_to_card = TimeToCardLog(Path(tempfile.mkdtemp(prefix="bench_ttc_")) / "ttc.jsonl")
serving_app.get_service = lambda guild_id="": _StubService()
rec = TestClient(serving_app.app)


def hdr(token):
    return {"X-Tenant-Token": token} if token else {}


def attack_class(label: str) -> str:
    """Group the existing attack labels without changing their requests."""
    lowered = label.lower()
    if any(marker in lowered for marker in ("garbage token", "empty token", "bit-flipped token", "truncated token")):
        return "forged/tampered tenant token"
    if "no token" in lowered or "without a token" in lowered:
        return "no-token attacks"
    if "vote" in lowered:
        return "faked votes"
    if "another guild" in lowered or "another guild's" in lowered:
        return "server-X-token-on-server-Y"
    return "other authorization/scope attacks"


MINE_RW = mint_token(MINE)
MINE_R = mint_token(MINE, scope=SCOPE_READ)
MINE_ME = mint_token(MINE, user_id=ME)
URLS = ["https://x.test/1"]


def run(client):
    get = lambda g, t: client.get(f"/api/events?guild_id={g}", headers=hdr(t))  # noqa: E731
    nights = lambda g, t: client.get(f"/api/nights?guild_id={g}", headers=hdr(t))  # noqa: E731
    delete = lambda g, t: client.delete(f"/api/events/{EVENT}?guild_id={g}", headers=hdr(t))  # noqa: E731

    def vote(g, t, user, delta=1):
        return client.post(f"/api/events/{EVENT}/vote?guild_id={g}",
                           json={"delta": delta, "user_id": user, "user_name": user}, headers=hdr(t))

    def went(g, t, user, happened=True):
        return client.post(f"/api/events/{EVENT}/went?guild_id={g}",
                           json={"user_id": user, "happened": happened}, headers=hdr(t))

    def body_post(path, g, t, **extra):
        return client.post(path, json={"guild_id": g, **extra}, headers=hdr(t))

    ingest = lambda g, t, u: body_post(  # noqa: E731
        "/api/ingest", g, t, urls=URLS, user_id=u
    )
    job = lambda g, t, u: body_post(  # noqa: E731
        "/api/jobs", g, t, urls=URLS, user_id=u
    )
    manual = lambda g, t: body_post("/api/manual", g, t, venue="Casa Loma", theme="birria tacos")  # noqa: E731
    edit = lambda g, t: body_post(f"/api/events/{EVENT}/edit", g, t, venue="Casa Loma", theme="birria tacos")  # noqa: E731
    lock = lambda g, t: body_post(f"/api/events/{EVENT}/lock", g, t, end_epoch=1_800_000_000)  # noqa: E731
    followups = lambda g, t: client.get(f"/api/followups?guild_id={g}", headers=hdr(t))  # noqa: E731
    share = lambda g, t: client.get(f"/share?guild_id={g}" + (f"&t={t}" if t else ""))  # noqa: E731
    recommend = lambda g, t: rec.post("/recommend", json={"channel_id": "c", "message": "tacos", "guild_id": g}, headers=hdr(t))  # noqa: E731
    plan = lambda g, t: rec.post("/plan", json={"channel_id": "c", "transcript": "a: tacos?", "guild_id": g}, headers=hdr(t))  # noqa: E731
    settings = lambda g, t: client.get(f"/api/settings?guild_id={g}", headers=hdr(t))  # noqa: E731
    survey = lambda g, t: client.post(  # noqa: E731
        "/api/survey", json={"guild_id": g, "answers": [3] * 10}, headers=hdr(t)
    )
    survey_results = lambda g, t: client.get(f"/api/survey?guild_id={g}", headers=hdr(t))  # noqa: E731
    feedback = lambda g, t: client.post(  # noqa: E731
        "/api/feedback", json={"guild_id": g, "kind": "bug", "text": "x"}, headers=hdr(t)
    )
    timing = lambda g, t: client.post(  # noqa: E731
        "/api/time-to-card", json={"guild_id": g, "seconds": 30, "outcome": "card"}, headers=hdr(t)
    )
    forget = lambda g, t: client.post(  # noqa: E731
        "/api/forget", json={"guild_id": g, "confirm": g}, headers=hdr(t)
    )
    save_settings = lambda g, t: client.put(  # noqa: E731
        "/api/settings", json={"guild_id": g, "home_city": "Long Beach, CA"}, headers=hdr(t)
    )

    # a real job in MINE, to probe reading its status from outside
    job_id = job(MINE, MINE_ME, ME).json()["job_id"]
    for _ in range(100):
        if client.get(f"/api/jobs/{job_id}", headers=hdr(MINE_RW)).json()["state"] == "done":
            break
        time.sleep(0.02)
    job_status = lambda t: client.get(f"/api/jobs/{job_id}", headers=hdr(t))  # noqa: E731
    failed_jobs = lambda g, t: client.get(f"/api/jobs?guild_id={g}&state=failed", headers=hdr(t))  # noqa: E731

    forged = [
        ("GET", "/api/events", "read another guild with my token", lambda: get(THEIRS, MINE_RW)),
        ("GET", "/api/events", "read another guild with no token", lambda: get(THEIRS, None)),
        ("GET", "/api/events", "read a DM stash with no token", lambda: get("dm-42", None)),
        ("GET", "/api/events", "read with a garbage token", lambda: get(MINE, "f" * 64)),
        ("GET", "/api/events", "read with an empty token", lambda: get(MINE, "")),
        ("GET", "/api/events", "read with a bit-flipped token", lambda: get(MINE, "f" + MINE_RW[1:])),
        ("GET", "/api/events", "read with a truncated token", lambda: get(MINE, MINE_RW[:32])),
        ("GET", "/api/nights", "read the nights counter of another guild", lambda: nights(THEIRS, MINE_RW)),
        ("DELETE", "/api/events/{event_id}", "delete in another guild", lambda: delete(THEIRS, MINE_RW)),
        ("DELETE", "/api/events/{event_id}", "delete with no token", lambda: delete(MINE, None)),
        ("DELETE", "/api/events/{event_id}", "delete using a read-only share token", lambda: delete(MINE, MINE_R)),
        ("POST", "/api/events/{event_id}/vote", "vote as another user", lambda: vote(MINE, MINE_ME, YOU)),
        ("POST", "/api/events/{event_id}/vote", "strip another user's vote", lambda: vote(MINE, MINE_ME, YOU, delta=-1)),
        ("POST", "/api/events/{event_id}/vote", "vote with a tenant token that omits the voter", lambda: vote(MINE, MINE_RW, ME)),
        ("POST", "/api/events/{event_id}/vote", "vote in another guild", lambda: vote(THEIRS, MINE_ME, ME)),
        ("POST", "/api/events/{event_id}/went", "confirm a night out as another user", lambda: went(MINE, MINE_ME, YOU)),
        ("POST", "/api/events/{event_id}/went", "dismiss a night out as another user", lambda: went(MINE, MINE_ME, YOU, happened=False)),
        ("POST", "/api/ingest", "ingest into another guild", lambda: ingest(THEIRS, MINE_ME, ME)),
        ("POST", "/api/ingest", "ingest with no token", lambda: ingest(MINE, None, ME)),
        ("POST", "/api/jobs", "queue a capture job in another guild", lambda: job(THEIRS, MINE_ME, ME)),
        ("POST", "/api/jobs", "evade the per-user limit by changing user_id", lambda: job(MINE, MINE_ME, YOU)),
        ("GET", "/api/jobs/{job_id}", "read another guild's capture job result", lambda: job_status(mint_token(THEIRS))),
        ("GET", "/api/jobs/{job_id}", "read a capture job result with no token", lambda: job_status(None)),
        ("GET", "/api/jobs", "list another guild's failed captures", lambda: failed_jobs(THEIRS, MINE_RW)),
        ("GET", "/api/jobs", "list failed captures with no token", lambda: failed_jobs(MINE, None)),
        ("POST", "/api/manual", "add a manual spot to another guild", lambda: manual(THEIRS, MINE_RW)),
        ("POST", "/api/manual", "add a manual spot with a read-only token", lambda: manual(MINE, MINE_R)),
        ("POST", "/api/events/{event_id}/edit", "edit a spot in another guild", lambda: edit(THEIRS, MINE_RW)),
        ("POST", "/api/events/{event_id}/lock", "lock in an event in another guild", lambda: lock(THEIRS, MINE_RW)),
        ("GET", "/api/followups", "swallow another guild's went-there prompts", lambda: followups(THEIRS, MINE_RW)),
        ("GET", "/api/followups", "swallow went-there prompts with a share token", lambda: followups(MINE, MINE_R)),
        ("GET", "/share", "open a share link with no token", lambda: share(MINE, None)),
        ("GET", "/share", "replay a share token against another guild", lambda: share(THEIRS, MINE_R)),
        ("POST", "/recommend", "query /recommend for another guild", lambda: recommend(THEIRS, MINE_RW)),
        ("POST", "/plan", "query /plan for another guild with no token", lambda: plan(THEIRS, None)),
        ("PUT", "/api/settings", "change another guild's /setup settings", lambda: save_settings(THEIRS, MINE_RW)),
        ("PUT", "/api/settings", "change /setup settings with a share token", lambda: save_settings(MINE, MINE_R)),
        ("GET", "/api/settings", "read /setup settings with a share token", lambda: settings(MINE, MINE_R)),
        ("POST", "/api/forget", "delete another guild's data", lambda: forget(THEIRS, MINE_RW)),
        ("POST", "/api/forget", "delete a guild's data with a share token", lambda: forget(MINE, MINE_R)),
        ("POST", "/api/forget", "delete a guild's data with no token", lambda: forget(MINE, None)),
        ("POST", "/api/survey", "stuff another guild's SUS survey", lambda: survey(THEIRS, MINE_RW)),
        ("GET", "/api/survey", "read another guild's SUS results", lambda: survey_results(THEIRS, MINE_RW)),
        ("GET", "/api/survey", "read SUS results with a share token", lambda: survey_results(MINE, MINE_R)),
        ("POST", "/api/feedback", "post feedback into another guild", lambda: feedback(THEIRS, MINE_RW)),
        ("POST", "/api/time-to-card", "pad the time-to-card numbers with no token", lambda: timing(MINE, None)),
        ("POST", "/api/time-to-card", "pad time-to-card with a share token", lambda: timing(MINE, MINE_R)),
    ]

    authentic = [
        ("read my own guild", lambda: get(MINE, MINE_RW)),
        ("read my own guild with a share token", lambda: get(MINE, MINE_R)),
        ("list my own failed captures", lambda: failed_jobs(MINE, MINE_RW)),
        ("read my nights counter", lambda: nights(MINE, MINE_RW)),
        ("delete in my own guild", lambda: delete(MINE, MINE_RW)),
        ("vote as myself", lambda: vote(MINE, MINE_ME, ME)),
        ("confirm a night out as myself", lambda: went(MINE, MINE_ME, ME)),
        ("read my own capture job", lambda: job_status(MINE_RW)),
        ("add a manual spot to my guild", lambda: manual(MINE, MINE_RW)),
        ("edit a spot in my guild", lambda: edit(MINE, MINE_RW)),
        ("lock in an event in my guild", lambda: lock(MINE, MINE_RW)),
        ("check my went-there prompts", lambda: followups(MINE, MINE_RW)),
        ("open my own share link", lambda: share(MINE, MINE_R)),
        ("query /recommend for my guild", lambda: recommend(MINE, MINE_RW)),
        ("query /plan for my guild", lambda: plan(MINE, MINE_RW)),
        ("save my /setup settings", lambda: save_settings(MINE, MINE_RW)),
        ("read my /setup settings", lambda: settings(MINE, MINE_RW)),
        ("answer the SUS survey in my guild", lambda: survey(MINE, MINE_RW)),
        ("read my guild's SUS results", lambda: survey_results(MINE, MINE_RW)),
        ("send feedback in my guild", lambda: feedback(MINE, MINE_RW)),
        ("report a time-to-card for my guild", lambda: timing(MINE, MINE_RW)),
        ("delete my own guild's data", lambda: forget(MINE, MINE_RW)),
        ("legacy single-tenant catalog (documented carve-out)", lambda: get("", None)),
    ]
    return forged, authentic


def main() -> None:
    with TestClient(admin.app) as client:
        forged, authentic = run(client)
        print("\nCatalog API cross-tenant authorization (admin :8010 + recommend :8003)\n")

        print("  forged requests (must all be REJECTED):")
        rejected = 0
        got_through = []
        by_class = {}
        for _, _, label, call in forged:
            code = call().status_code
            ok = code in (401, 403)
            category = attack_class(label)
            counts = by_class.setdefault(category, {"attacks": 0, "refused": 0, "got_through": []})
            counts["attacks"] += 1
            rejected += ok
            if not ok:
                got_through.append(label)
                counts["got_through"].append(label)
            else:
                counts["refused"] += 1
            print(f"    {'REJECTED' if ok else f'ACCEPTED {code} <-- FAIL'}  {label}")

        print("\n  authentic requests (must all be ACCEPTED):")
        accepted = 0
        for label, call in authentic:
            code = call().status_code
            ok = code in (200, 202)
            accepted += ok
            print(f"    {'ACCEPTED' if ok else f'REJECTED {code} <-- FAIL'}  {label}")

    print(
        f"\n  => {len(forged)} attacks, {rejected} refused, "
        f"{len(got_through)} got through"
    )
    if got_through:
        print("  => got through: " + "; ".join(got_through))
    print("\n  results by attack class:")
    for category, counts in sorted(by_class.items()):
        print(
            f"    {category}: {counts['attacks']} attacks, "
            f"{counts['refused']} refused, {len(counts['got_through'])} got through"
        )
        if counts["got_through"]:
            print("      got through: " + "; ".join(counts["got_through"]))
    print(f"  => accepted {accepted}/{len(authentic)} authentic requests")
    if "unclassified" in by_class or rejected != len(forged) or accepted != len(authentic):
        print("  => FIX FAILURES BEFORE CLAIMING A NUMBER\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
