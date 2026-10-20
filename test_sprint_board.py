"""Offline tests for scripts/sprint_board.py, against an in-memory fake of Discord's REST API."""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import sprint_board as sb  # noqa: E402

GUILD = "1"
NOW = datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc)


class FakeDiscord:
    """Just enough of Discord to run the script: channels, forum posts, messages."""

    def __init__(self, now=NOW):
        self.now = now
        self.seq = 0
        self.channels = {}   # id -> channel or thread
        self.messages = {}   # channel id -> [message]
        self.calls = []
        self.rate_limit_next = 0

    def new_id(self, at=None):
        self.seq += 1
        return str(sb.snowflake_at(at or self.now) + self.seq)

    def add_forum(self, name="sprint-board", tags=()):
        fid = self.new_id()
        self.channels[fid] = {"id": fid, "type": sb.FORUM_TYPE, "name": name,
                              "available_tags": [{"id": self.new_id(), "name": t} for t in tags]}
        return self.channels[fid]

    def add_post(self, forum, name, tags, at, archived=False, replies=()):
        ids = sb.tag_ids(forum)
        tid = self.new_id(at)
        self.channels[tid] = {"id": tid, "parent_id": forum["id"], "name": name, "type": 11,
                              "applied_tags": [ids[t] for t in tags], "last_message_id": tid,
                              "message_count": len(replies),
                              "thread_metadata": {"archived": archived,
                                                  "archive_timestamp": at.isoformat()}}
        self.messages[tid] = [{"id": tid, "content": "opening", "author": {"username": "bot", "bot": True}}]
        for who, when, text in replies:
            mid = self.new_id(when)
            self.messages[tid].append({"id": mid, "content": text,
                                       "author": {"username": who.lower(), "global_name": who}})
            self.channels[tid]["last_message_id"] = mid
        return self.channels[tid]

    def _assign_tag_ids(self, tags):
        return [t if "id" in t else {**t, "id": self.new_id()} for t in tags]

    def __call__(self, method, url, headers, body):
        assert headers["Authorization"].startswith("Bot ")
        data = json.loads(body) if body else None
        self.calls.append((method, url, data))
        if self.rate_limit_next:
            self.rate_limit_next -= 1
            return 429, {}, json.dumps({"retry_after": 0.5}).encode()
        u = urlparse(url)
        path, query = u.path.removeprefix("/api/v10"), parse_qs(u.query)
        parts = path.strip("/").split("/")
        result = self.route(method, parts, query, data)
        if isinstance(result, tuple):
            return result
        return 200, {}, json.dumps(result).encode()

    def route(self, method, parts, query, data):
        if parts[0] == "guilds" and parts[2] == "channels":
            if method == "GET":
                return [c for c in self.channels.values() if "parent_id" not in c]
            cid = self.new_id()
            self.channels[cid] = {"id": cid, **data,
                                  "available_tags": self._assign_tag_ids(data["available_tags"])}
            return self.channels[cid]
        if parts[0] == "guilds" and parts[2] == "threads":
            return {"threads": [c for c in self.channels.values()
                                if "parent_id" in c and not c["thread_metadata"]["archived"]]}
        cid = parts[1]
        if len(parts) == 2 and method == "GET":
            return self.channels.get(cid) or (404, {}, b'{"message": "Unknown Channel"}')
        if len(parts) == 2 and method == "PATCH":
            if "available_tags" in data:
                data = {**data, "available_tags": self._assign_tag_ids(data["available_tags"])}
            self.channels[cid].update(data)
            if "archived" in data:
                self.channels[cid]["thread_metadata"]["archived"] = data["archived"]
            return self.channels[cid]
        if parts[2] == "threads" and method == "GET":  # archived/public
            return {"threads": [c for c in self.channels.values() if c.get("parent_id") == cid
                                and c["thread_metadata"]["archived"]], "has_more": False}
        if parts[2] == "threads":
            forum = self.channels[cid]
            post = self.add_post(forum, data["name"], [], self.now)
            post["applied_tags"] = data["applied_tags"]
            self.messages[post["id"]][0]["content"] = data["message"]["content"]
            return post
        if parts[2] == "messages" and method == "GET":
            after = int(query.get("after", ["0"])[0])
            return [m for m in reversed(self.messages[cid]) if int(m["id"]) > after]
        if parts[2] == "messages":
            mid = self.new_id()
            self.messages[cid].append({"id": mid, "content": data["content"],
                                       "author": {"username": "bot", "bot": True}})
            return self.messages[cid][-1]
        raise AssertionError(f"unexpected call {method} {parts}")


def all_tags():
    return [t["name"] for t in sb.wanted_tags()]


@pytest.fixture
def fake():
    return FakeDiscord()


@pytest.fixture
def dc(fake):
    return sb.Discord("test-token", send=fake, sleep=lambda s: None)


# --- parsing --------------------------------------------------------------------------------

PLAN = """# Track A

### Phase 2 — Oct 6 – Oct 24 (100 pts)

**#2 Authenticated capture + fallback chain (100)**
- [x] Define one fetch interface so any source can be swapped
      (incl. a paid lookup later).
- [ ] Order the chain.
- [ ] **Done when:** the harness number goes up.

### Phase 3 — Oct 27 – Nov 14 (100 pts)

**#15 Gatekept-reel recovery ★ (100)**
- [ ] Pull the top ~10 comments.
"""


def test_parse_features_reads_one_phase_and_joins_wrapped_lines(tmp_path):
    path = tmp_path / "TRACK-A-CAPTURE.md"
    path.write_text(PLAN, encoding="utf-8", newline="\n")
    [f] = sb.parse_features(path, 2)
    assert (f.number, f.title, f.points, f.track) == (2, "Authenticated capture + fallback chain", 100, "A")
    assert f.name == "#2 Authenticated capture + fallback chain (100 pts)"
    assert f.items[0] == (True, "Define one fetch interface so any source can be swapped "
                                "(incl. a paid lookup later).")
    assert f.status == "Doing"  # one box ticked
    assert [g.number for g in sb.parse_features(path, 3)] == [15]


def test_parse_features_skips_files_that_are_not_track_plans(tmp_path):
    path = tmp_path / "TRACK-D-SSRF-FINDINGS.md"
    path.write_text(PLAN, encoding="utf-8", newline="\n")
    assert sb.parse_features(path, 2) == []


def test_real_track_plans_have_phase_2_features_for_every_track():
    feats = sb.all_features(2)
    assert {f.track for f in feats} == set(sb.TRACKS)
    assert all(len(sb.feature_body(f)) <= sb.MAX_MESSAGE for f in feats)


def test_phase_calendar_and_current_phase():
    cal = sb.phase_calendar(2026)
    assert cal[2] == (date(2026, 10, 6), date(2026, 10, 24))
    assert sb.current_phase(cal, date(2026, 10, 16)) == 2
    assert sb.current_phase(cal, date(2026, 10, 25)) == 3   # between phases: the next one
    assert sb.current_phase(cal, date(2026, 12, 25)) == 4   # after the last: the last


def test_snowflakes_round_trip_to_the_millisecond():
    assert sb.snowflake_time(sb.snowflake_at(NOW)) == NOW


def test_chunks_respects_the_message_limit():
    text = "\n".join(["x" * 900] * 5)
    parts = sb.chunks(text)
    assert len(parts) == 3 and all(len(p) <= sb.MAX_MESSAGE for p in parts)
    assert "\n".join(parts) == text


# --- talking to Discord -----------------------------------------------------------------------

def test_setup_creates_the_forum_with_every_tag_then_is_idempotent(fake, dc):
    forum, msg = sb.setup(dc, GUILD, "sprint-board")
    assert msg == "created #sprint-board"
    assert [t["name"] for t in forum["available_tags"]] == all_tags()
    assert len(all_tags()) <= 20  # Discord's limit for one forum
    assert sb.setup(dc, GUILD, "sprint-board")[1] == "#sprint-board already set up"


def test_setup_adds_missing_tags_and_keeps_existing_ones(fake, dc):
    old = fake.add_forum(tags=["Backlog", "Our own tag"])
    kept = {t["name"]: t["id"] for t in old["available_tags"]}
    forum, msg = sb.setup(dc, GUILD, "sprint-board")
    assert msg == f"added {len(all_tags()) - 1} tags to #sprint-board"
    now = {t["name"]: t["id"] for t in forum["available_tags"]}
    assert now["Our own tag"] == kept["Our own tag"] and now["Backlog"] == kept["Backlog"]


def test_hand_made_tags_match_whatever_their_case_or_separator(fake, dc):
    # The board made by hand has "doing", "phase 2" and "A - Capture": setup must not add a
    # second copy of those, and the report must read them as the script's own tags.
    forum = fake.add_forum(tags=["doing", "phase 2", "A - Capture", "Standup"])
    fake.add_post(forum, "#2 Capture chain (100 pts)", ["Doing", "A · Capture", "Phase 2"], NOW)
    sb.setup(dc, GUILD, "sprint-board")
    names = [t["name"] for t in fake.channels[forum["id"]]["available_tags"]]
    assert len(names) == len(all_tags()) and "Doing" not in names and "doing" in names
    post = sb.collect(dc, GUILD, fake.channels[forum["id"]], None)[0]
    assert (post.status, post.track, post.phase) == ("Doing", "A · Capture", 2)


def test_seed_posts_each_feature_once_with_status_track_and_phase(fake, dc, tmp_path):
    forum = fake.add_forum(tags=all_tags())
    path = tmp_path / "TRACK-A-CAPTURE.md"
    path.write_text(PLAN, encoding="utf-8", newline="\n")
    feats = sb.parse_features(path, 2)
    assert sb.seed(dc, GUILD, forum, feats, apply=False)[0].startswith("would post")
    assert not any(m == "POST" for m, _, _ in fake.calls)  # a dry run posts nothing
    sb.seed(dc, GUILD, forum, feats, apply=True)
    [post] = [c for c in fake.channels.values() if c.get("parent_id") == forum["id"]]
    names = sb.tag_names(forum)
    assert sorted(names[t] for t in post["applied_tags"]) == ["A · Capture", "Doing", "Phase 2"]
    assert sb.seed(dc, GUILD, forum, feats, apply=True)[0].startswith("skip")


def test_every_message_the_script_sends_pings_nobody(fake, dc):
    forum = fake.add_forum(tags=all_tags())
    sb.new_post(dc, forum, "Digest", "x\n" * 1500, ["Digest"])  # long: opening + replies
    sb.standup(dc, GUILD, forum, NOW.date())
    sent = [d for m, _, d in fake.calls if m == "POST"]
    contents = [d.get("message", d) for d in sent]
    assert len(contents) >= 3
    assert all(c["allowed_mentions"] == {"parse": []} for c in contents)
    assert all(len(c["content"]) <= sb.MAX_MESSAGE for c in contents)


def test_standup_opens_one_post_per_day(fake, dc):
    forum = fake.add_forum(tags=all_tags())
    first = sb.standup(dc, GUILD, forum, date(2026, 10, 16))
    again = sb.standup(dc, GUILD, forum, date(2026, 10, 16))
    assert first["id"] == again["id"] and first["name"] == "Standup — Fri Oct 16"


def test_team_date_is_pacific_time_across_daylight_saving():
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
    assert sb.team_date(utc(2026, 10, 20, 4, 8)) == date(2026, 10, 19)   # Mon 9:08 p.m. PDT
    assert sb.team_date(utc(2026, 10, 20, 15, 51)) == date(2026, 10, 20)  # the 8:51 a.m. run
    assert sb.team_date(utc(2026, 11, 1, 8, 59)) == date(2026, 11, 1)    # 1:59 a.m. PDT
    assert sb.team_date(utc(2026, 11, 2, 7, 30)) == date(2026, 11, 1)    # 11:30 p.m. PST
    assert sb.team_date(utc(2026, 3, 8, 9, 59)) == date(2026, 3, 8)      # 1:59 a.m. PST
    assert sb.team_date(utc(2026, 1, 1, 7, 59)) == date(2025, 12, 31)


def test_an_evening_standup_gets_the_pacific_date(fake, dc, capsys):
    fake.add_forum(tags=all_tags())
    assert sb.main(["standup"], dc=dc, now=datetime(2026, 10, 20, 4, 8, tzinfo=timezone.utc)) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "Standup — Mon Oct 19"


def test_move_swaps_only_the_status_tag_and_reopens_archived_posts(fake, dc):
    forum = fake.add_forum(tags=all_tags())
    post = fake.add_post(forum, "#2 Capture (100 pts)", ["Done", "A · Capture", "Phase 2"],
                         NOW, archived=True)
    assert sb.move(dc, forum, post["id"], "Review") == "#2 Capture (100 pts) -> Review"
    names = sb.tag_names(forum)
    assert sorted(names[t] for t in post["applied_tags"]) == ["A · Capture", "Phase 2", "Review"]
    assert post["thread_metadata"]["archived"] is False


def test_rate_limit_waits_and_retries(fake):
    waits = []
    dc = sb.Discord("t", send=fake, sleep=waits.append)
    fake.rate_limit_next = 2
    assert dc.call("GET", f"/guilds/{GUILD}/channels") == []
    assert waits == [0.5, 0.5]


def test_errors_name_the_call_but_never_the_token(fake, dc):
    with pytest.raises(sb.DiscordError) as err:
        dc.call("GET", "/channels/999")
    assert "404" in str(err.value) and "test-token" not in str(err.value)


# --- the report -------------------------------------------------------------------------------

def board(fake):
    forum = fake.add_forum(tags=all_tags())
    day = timedelta(days=1)
    fake.add_post(forum, "#2 Capture chain (100 pts)", ["Doing", "A · Capture", "Phase 2"],
                  NOW - 9 * day)  # quiet 9 days: stale
    fake.add_post(forum, "#16 Category classifier (100 pts)", ["Blocked", "B · Accuracy", "Phase 2"],
                  NOW - 4 * day, replies=[("Valeria", NOW - day, "Blocked: waiting on labels")])
    fake.add_post(forum, "#20 Feedback survey (40 pts)", ["Done", "C · Experience", "Phase 2"],
                  NOW - 20 * day, archived=True)
    fake.add_post(forum, "#30 Session hardening (100 pts)", ["Backlog", "D · Security", "Phase 2"],
                  NOW - 10 * day)
    fake.add_post(forum, "#11 Staging (100 pts)", ["Backlog", "N · Platform", "Phase 3"], NOW)
    fake.add_post(forum, "Standup — Wed Oct 14", ["Standup"], NOW - 2 * day,
                  replies=[("Kimberly", NOW - 2 * day, "Did: attack suite\nBlocked: none")])
    return forum


def test_collect_reads_archived_posts_and_only_peoples_recent_replies(fake, dc):
    forum = board(fake)
    posts = sb.collect(dc, GUILD, forum, NOW - timedelta(days=7))
    assert len(posts) == 6  # the archived Done post is included
    by = {p.name: p for p in posts}
    assert by["#16 Category classifier (100 pts)"].recent == [
        {"author": "Valeria", "at": (NOW - timedelta(days=1)).isoformat(),
         "text": "Blocked: waiting on labels"}]
    assert by["#2 Capture chain (100 pts)"].recent == []  # quiet: replies not fetched
    assert by["Standup — Wed Oct 14"].kind == "Standup"


def test_render_counts_points_for_this_phase_and_flags_stale_work(fake, dc):
    forum = board(fake)
    posts = sb.collect(dc, GUILD, forum, None)
    text = sb.render(posts, 2, (date(2026, 10, 6), date(2026, 10, 24)), NOW)
    assert text.startswith("**Phase 2 · Oct 6 – Oct 24 · 8 days left**")
    assert "Points done 40 / 340 (12%)" in text      # Phase 3 and the standup don't count
    assert "#2 Capture chain (100 pts) · A · Capture · quiet 9 d ⚠️ stale" in text
    assert "**🚧 Blocked**" in text and "#11 Staging" not in text
    assert "|" not in text  # Discord renders no tables


def test_report_json_is_what_the_routine_reads(fake, dc):
    board(fake)
    out = io_main(["report", "--json"], dc)
    data = json.loads(out)
    assert data["phase"] == 2 and len(data["posts"]) == 6
    assert {"name", "status", "track", "points", "last", "recent"} <= set(data["posts"][0])
    assert data["warning"] is None


def test_report_warns_when_discord_hides_reply_text(fake, dc, capsys):
    # Without the Message Content Intent, Discord's REST API returns people's messages with
    # empty content. The routine must say so rather than report that nobody wrote anything.
    forum = board(fake)
    fake.add_post(forum, "#17 Eval report (30 pts)", ["Doing", "B · Accuracy", "Phase 2"],
                  NOW - timedelta(days=2), replies=[("Valeria", NOW - timedelta(days=1), "")])
    post = fake.add_post(forum, "#18 Time to card (30 pts)", ["Doing", "C · Experience", "Phase 2"],
                         NOW - timedelta(days=2), replies=[("Nick", NOW - timedelta(days=1), "")])
    fake.messages[post["id"]][-1]["attachments"] = [{"id": "9", "filename": "chart.png"}]
    data = json.loads(io_main(["report", "--json"], dc))
    assert data["warning"].startswith("Discord returned 1 reply without text")
    assert "Message Content Intent" in capsys.readouterr().err
    texts = {p["name"]: [r["text"] for r in p["recent"]] for p in data["posts"]}
    assert texts["#18 Time to card (30 pts)"] == ["(an attachment, no text)"]


def io_main(argv, dc):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert sb.main(argv, dc=dc, now=NOW) == 0
    return buf.getvalue()


def test_main_without_secrets_explains_and_fails(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SPRINT_GUILD_ID", raising=False)
    assert sb.main(["report"], now=NOW) == 2
    assert "DISCORD_BOT_TOKEN" in capsys.readouterr().err
    assert sb.main(["seed", "--phase", "2"], now=NOW) == 0  # a dry run needs no secrets
    assert "would post" in capsys.readouterr().out


def test_main_before_setup_says_to_run_setup(fake, dc, capsys):
    assert sb.main(["report"], dc=dc, now=NOW) == 1
    assert "run `sprint_board.py setup` first" in capsys.readouterr().err
