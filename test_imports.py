"""The Instagram import backlog (serving/imports.py) and its drip into the capture queue."""
import asyncio

import pytest

from src.ingestion.serving import admin
from src.ingestion.serving.imports import (
    MAX_PER_REQUEST,
    ImportBacklog,
    ImportRequestError,
    canonical,
)
from src.ingestion.serving.jobs import JobQueue

R = "https://www.instagram.com/reel/{}/"


@pytest.mark.parametrize("raw, want", [
    ("https://www.instagram.com/reel/ABCDE12345/", R.format("ABCDE12345")),
    ("https://instagram.com/p/ABCDE12345?igsh=xyz", R.format("ABCDE12345")),
    ("http://m.instagram.com/reels/ABC_de-123/", R.format("ABC_de-123")),
    (" https://www.instagram.com/tv/ABCDE12345/ ", R.format("ABCDE12345")),
    ("https://www.instagram.com/stories/someone/123/", None),
    ("https://evil.test/reel/ABCDE12345/", None),
    ("https://www.instagram.com.evil.test/reel/ABCDE12345/", None),
    ("http://127.0.0.1/reel/ABCDE12345/", None),
    ("", None),
])
def test_only_instagram_post_links_are_kept(raw, want):
    assert canonical(raw) == want


def test_add_dedupes_and_counts():
    b = ImportBacklog()
    r = b.add("g1", "u1", [R.format("AAAAA1"), R.format("AAAAA1"), "https://x.test/", R.format("BBBBB2")])
    assert r == {"added": 2, "duplicates": 1, "rejected": 1, "pending": 2}
    # a second import of the same file adds nothing, even after the links were sent
    b.take(10)
    assert b.add("g1", "u1", [R.format("AAAAA1")])["duplicates"] == 1


def test_request_size_is_capped():
    with pytest.raises(ImportRequestError):
        ImportBacklog().add("g", "u", [R.format("AAAAA1")] * (MAX_PER_REQUEST + 1))


def test_take_is_oldest_first_one_user_per_batch_and_servers_take_turns():
    b = ImportBacklog()
    b.add("g1", "u1", [R.format(f"G1AAA{i}") for i in range(4)])
    b.add("g1", "u2", [R.format("G1BBB0")])
    b.add("g2", "u3", [R.format("G2AAA0")])
    first = b.take(3)
    second = b.take(3)
    assert first == ("g1", "u1", [R.format(f"G1AAA{i}") for i in range(3)])
    assert second == ("g2", "u3", [R.format("G2AAA0")])
    assert b.take(3) == ("g1", "u1", [R.format("G1AAA3")])
    assert b.take(3) == ("g1", "u2", [R.format("G1BBB0")])
    assert b.take(3) is None
    assert b.status("g1") == {"pending": 0, "sent": 5}


def test_put_back_returns_links_to_the_front():
    b = ImportBacklog()
    b.add("g", "u", [R.format(f"AAAAA{i}") for i in range(3)])
    gid, user, urls = b.take(2)
    b.put_back(gid, user, urls)
    assert b.take(3)[2] == [R.format(f"AAAAA{i}") for i in range(3)]


def test_backlog_survives_a_restart_and_forget_clears_it(tmp_path):
    path = tmp_path / "imports.json"
    ImportBacklog(path).add("g", "u", [R.format("AAAAA1"), R.format("BBBBB2")])
    again = ImportBacklog(path)
    assert again.status("g")["pending"] == 2
    assert again.forget_guild("g") == 2
    assert ImportBacklog(path).status("g") == {"pending": 0, "sent": 0}
    assert "AAAAA1" not in path.read_text(encoding="utf-8")


def test_tick_feeds_the_queue_only_when_it_is_idle(monkeypatch):
    async def never_finishes(urls, guild_id):
        await asyncio.sleep(3600)

    async def scenario():
        jobs = JobQueue(never_finishes)
        backlog = ImportBacklog()
        backlog.add("g", "u", [R.format(f"AAAAA{i}") for i in range(5)])
        monkeypatch.setattr(admin, "_jobs", jobs)
        monkeypatch.setattr(admin, "_imports", backlog)
        monkeypatch.setattr(admin, "_IMPORT_BATCH", 3)
        monkeypatch.setattr(admin, "_IMPORT_MAX_ACTIVE", 1)
        job = admin._import_tick()
        assert job is not None and job.guild_id == "g" and len(job.urls) == 3
        assert admin._import_tick() is None             # the queue is busy: wait
        assert backlog.status("g") == {"pending": 2, "sent": 3}
        jobs.shutdown()

    asyncio.run(scenario())
