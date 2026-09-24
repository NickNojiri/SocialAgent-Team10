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
from src.ingestion.serving.guild_settings import GuildSettingsStore  # noqa: E402
from src.ingestion.serving.jobs import JobQueue  # noqa: E402
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
serving_app.get_service = lambda guild_id="": _StubService()
rec = TestClient(serving_app.app)


def hdr(token):
    return {"X-Tenant-Token": token} if token else {}


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
        ("read another guild with my token", lambda: get(THEIRS, MINE_RW)),
        ("read another guild with no token", lambda: get(THEIRS, None)),
        ("read a DM stash with no token", lambda: get("dm-42", None)),
        ("read with a garbage token", lambda: get(MINE, "f" * 64)),
        ("read with an empty token", lambda: get(MINE, "")),
        ("read with a bit-flipped token", lambda: get(MINE, "f" + MINE_RW[1:])),
        ("read with a truncated token", lambda: get(MINE, MINE_RW[:32])),
        ("read the nights counter of another guild", lambda: nights(THEIRS, MINE_RW)),
        ("delete in another guild", lambda: delete(THEIRS, MINE_RW)),
        ("delete with no token", lambda: delete(MINE, None)),
        ("delete using a read-only share token", lambda: delete(MINE, MINE_R)),
        ("vote as another user", lambda: vote(MINE, MINE_ME, YOU)),
        ("strip another user's vote", lambda: vote(MINE, MINE_ME, YOU, delta=-1)),
        ("vote with a tenant token that omits the voter", lambda: vote(MINE, MINE_RW, ME)),
        ("vote in another guild", lambda: vote(THEIRS, MINE_ME, ME)),
        ("confirm a night out as another user", lambda: went(MINE, MINE_ME, YOU)),
        ("dismiss a night out as another user", lambda: went(MINE, MINE_ME, YOU, happened=False)),
        ("ingest into another guild", lambda: ingest(THEIRS, MINE_ME, ME)),
        ("ingest with no token", lambda: ingest(MINE, None, ME)),
        ("queue a capture job in another guild", lambda: job(THEIRS, MINE_ME, ME)),
        ("evade the per-user limit by changing user_id", lambda: job(MINE, MINE_ME, YOU)),
        ("read another guild's capture job result", lambda: job_status(mint_token(THEIRS))),
        ("read a capture job result with no token", lambda: job_status(None)),
        ("list another guild's failed captures", lambda: failed_jobs(THEIRS, MINE_RW)),
        ("list failed captures with no token", lambda: failed_jobs(MINE, None)),
        ("add a manual spot to another guild", lambda: manual(THEIRS, MINE_RW)),
        ("add a manual spot with a read-only token", lambda: manual(MINE, MINE_R)),
        ("edit a spot in another guild", lambda: edit(THEIRS, MINE_RW)),
        ("lock in an event in another guild", lambda: lock(THEIRS, MINE_RW)),
        ("swallow another guild's went-there prompts", lambda: followups(THEIRS, MINE_RW)),
        ("swallow went-there prompts with a share token", lambda: followups(MINE, MINE_R)),
        ("open a share link with no token", lambda: share(MINE, None)),
        ("replay a share token against another guild", lambda: share(THEIRS, MINE_R)),
        ("query /recommend for another guild", lambda: recommend(THEIRS, MINE_RW)),
        ("query /plan for another guild with no token", lambda: plan(THEIRS, None)),
        ("change another guild's /setup settings", lambda: save_settings(THEIRS, MINE_RW)),
        ("change /setup settings with a share token", lambda: save_settings(MINE, MINE_R)),
        ("read /setup settings with a share token", lambda: settings(MINE, MINE_R)),
        ("delete another guild's data", lambda: forget(THEIRS, MINE_RW)),
        ("delete a guild's data with a share token", lambda: forget(MINE, MINE_R)),
        ("delete a guild's data with no token", lambda: forget(MINE, None)),
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
        for label, call in forged:
            code = call().status_code
            ok = code in (401, 403)
            rejected += ok
            print(f"    {'REJECTED' if ok else f'ACCEPTED {code} <-- FAIL'}  {label}")

        print("\n  authentic requests (must all be ACCEPTED):")
        accepted = 0
        for label, call in authentic:
            code = call().status_code
            ok = code in (200, 202)
            accepted += ok
            print(f"    {'ACCEPTED' if ok else f'REJECTED {code} <-- FAIL'}  {label}")

    print(
        f"\n  => rejected {rejected}/{len(forged)} forged requests across "
        f"{len(forged)} attack classes, accepted {accepted}/{len(authentic)} authentic"
    )
    if rejected != len(forged) or accepted != len(authentic):
        print("  => FIX FAILURES BEFORE CLAIMING A NUMBER\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
