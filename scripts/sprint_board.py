"""Sprint board: the team's agile board, kept as a Discord forum channel.

One forum, #sprint-board. Every feature in a track plan (docs/tracks/TRACK-*.md) is a post.
Its tags say where it stands: a status (Backlog, Doing, Review, Blocked, Done), its track
and its phase. Standups and the weekly digest are posts in the same forum. A Claude routine
runs this script Mon/Wed/Fri (docs/SPRINT_BOARD.md has the whole loop).

    python scripts/sprint_board.py setup                  # create the forum and its tags
    python scripts/sprint_board.py seed --phase 2         # one post per feature (--apply to post)
    python scripts/sprint_board.py report --since 7       # the board as Discord markdown, or --json
    python scripts/sprint_board.py standup                # open today's standup post
    python scripts/sprint_board.py post --title T --file F   # a new post (tagged Digest)
    python scripts/sprint_board.py post --into ID --file F   # a reply inside an existing post
    python scripts/sprint_board.py move POST_ID Review    # change a post's status tag

Needs DISCORD_BOT_TOKEN and SPRINT_GUILD_ID in the environment (a seed dry run needs
neither). Standard library only, so a fresh routine session runs it without installing
anything. It never pings anyone: every message sends allowed_mentions with nothing allowed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRACK_DIR = REPO / "docs" / "tracks"

API = "https://discord.com/api/v10"
FORUM_TYPE = 15                    # Discord channel type for a forum
DISCORD_EPOCH_MS = 1420070400000   # snowflake ids count milliseconds from here
MAX_MESSAGE = 2000                 # Discord's limit for one message
NO_PINGS = {"parse": []}           # docs/THREAT_MODEL.md T1: the bots never ping
STALE_DAYS = 5                     # a Doing post this quiet gets flagged

STATUSES = ["Backlog", "Doing", "Review", "Blocked", "Done"]
TRACKS = {"A": "A · Capture", "B": "B · Accuracy", "C": "C · Experience",
          "D": "D · Security", "N": "N · Platform"}
KINDS = ["Standup", "Digest"]      # posts that are not features
EMOJI = {"Backlog": "📋", "Doing": "🔧", "Review": "👀", "Blocked": "🚧", "Done": "✅",
         "Standup": "📣", "Digest": "📰"}
TOPIC = ("One post per feature. Tags: status, track, phase. Reply with progress and change "
         "the status tag when it moves. Standups: reply Did / Doing / Blocked. "
         "Guide: docs/SPRINT_BOARD.md")


class DiscordError(RuntimeError):
    pass


def urllib_send(method: str, url: str, headers: dict, body: bytes | None):
    """The real transport. Tests pass a fake with the same signature."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


class Discord:
    """A thin client for Discord's REST API, authenticated as a bot."""

    def __init__(self, token: str, send=urllib_send, sleep=time.sleep):
        self._headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": "DiscordBot (https://github.com/NickNojiri/SocialAgent-Team10, 1.0)",
            "Content-Type": "application/json",
        }
        self._send = send
        self._sleep = sleep

    def call(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        for _ in range(4):
            status, _headers, raw = self._send(method, API + path, self._headers, data)
            if status == 429:  # rate limited: Discord says how many seconds to wait
                wait = json.loads(raw or b"{}").get("retry_after", 1)
                self._sleep(min(float(wait), 10.0))
                continue
            if status >= 400:  # the path never holds the token, so it is safe to show
                detail = raw[:200].decode("utf-8", "replace")
                raise DiscordError(f"{method} {path} -> {status}: {detail}")
            return json.loads(raw) if raw else None
        raise DiscordError(f"{method} {path}: still rate limited after 4 tries")


# --- time helpers -------------------------------------------------------------------------

def snowflake_time(snowflake) -> datetime:
    """When a Discord id was made: its top bits are milliseconds since the Discord epoch."""
    return datetime.fromtimestamp(((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000,
                                  tz=timezone.utc)


def team_date(now: datetime) -> date:
    """Today's date in Long Beach (US Pacific), so an evening run doesn't title tomorrow.

    US daylight time runs from 2 a.m. on the second Sunday in March to 2 a.m. on the first
    Sunday in November. Worked out by hand because Windows Python has no tz database.
    """
    march8, nov1 = date(now.year, 3, 8), date(now.year, 11, 1)
    dst_start = datetime.combine(march8 + timedelta(days=(6 - march8.weekday()) % 7),
                                 datetime.min.time(), timezone.utc) + timedelta(hours=10)
    dst_end = datetime.combine(nov1 + timedelta(days=(6 - nov1.weekday()) % 7),
                               datetime.min.time(), timezone.utc) + timedelta(hours=9)
    offset = -7 if dst_start <= now < dst_end else -8
    return (now + timedelta(hours=offset)).date()


def snowflake_at(when: datetime) -> int:
    """The smallest id made at `when`, for 'messages after this time' queries."""
    return (int(when.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22


CAL_ROW = re.compile(r"^\|\s*(\d)\s*\|\s*([A-Z][a-z]{2} \d{1,2})\s*[–-]\s*([A-Z][a-z]{2} \d{1,2})\s*\|")


def phase_calendar(year: int, text: str | None = None) -> dict[int, tuple[date, date]]:
    """Phase number -> (first day, last day), read from the table in docs/tracks/README.md."""
    if text is None:
        text = (TRACK_DIR / "README.md").read_text(encoding="utf-8")
    cal = {}
    for line in text.split("\n"):
        m = CAL_ROW.match(line)
        if m:
            start, end = (datetime.strptime(f"{d} {year}", "%b %d %Y").date() for d in m.group(2, 3))
            cal[int(m.group(1))] = (start, end)
    return cal


def current_phase(cal: dict[int, tuple[date, date]], today: date) -> int:
    """The phase `today` falls in; between phases, the next one; after the last, the last."""
    for n in sorted(cal):
        if today <= cal[n][1]:
            return n
    return max(cal)


# --- features from the track plans --------------------------------------------------------

FEATURE = re.compile(r"^\*\*#(\d+) (.+?) \((\d+)\)\*\*$")
PHASE_HEAD = re.compile(r"^### Phase (\d+)\b")
HEADING = re.compile(r"^#{1,3} ")
CHECKBOX = re.compile(r"^- \[([ xX])\] (.*)")
TRACK_FILE = re.compile(r"^TRACK-([A-Z])-[A-Z]+\.md$")


@dataclass
class Feature:
    number: int
    title: str
    points: int
    track: str
    phase: int
    source: str
    items: list[tuple[bool, str]] = field(default_factory=list)

    @property
    def name(self) -> str:  # a forum post title holds at most 100 characters
        return f"#{self.number} {self.title} ({self.points} pts)"[:100]

    @property
    def status(self) -> str:
        """Starting status from the plan's checkboxes: none ticked is Backlog, some is Doing."""
        ticked = [done for done, _ in self.items]
        if ticked and all(ticked):
            return "Done"
        return "Doing" if any(ticked) else "Backlog"


def parse_features(path: Path, phase: int) -> list[Feature]:
    """The features under '### Phase <phase>' in one TRACK-X-NAME.md plan."""
    m = TRACK_FILE.match(path.name)
    if not m or m.group(1) not in TRACKS:
        return []
    feats: list[Feature] = []
    in_phase, cur = False, None
    for line in path.read_text(encoding="utf-8").split("\n"):
        if HEADING.match(line):
            head = PHASE_HEAD.match(line)
            in_phase = bool(head) and int(head.group(1)) == phase
            cur = None
            continue
        if not in_phase:
            continue
        f = FEATURE.match(line.strip())
        if f:
            cur = Feature(int(f[1]), f[2], int(f[3]), m.group(1), phase,
                          f"docs/tracks/{path.name}")
            feats.append(cur)
        elif cur is not None:
            box = CHECKBOX.match(line)
            if box:
                cur.items.append((box[1] != " ", box[2].strip()))
            elif line.startswith("  ") and line.strip() and cur.items:  # a wrapped checkbox line
                done, text = cur.items[-1]
                cur.items[-1] = (done, f"{text} {line.strip()}")
    return feats


def all_features(phase: int, track_dir: Path = TRACK_DIR) -> list[Feature]:
    return [f for path in sorted(track_dir.glob("TRACK-*.md")) for f in parse_features(path, phase)]


def clip(text: str, limit: int = MAX_MESSAGE) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def feature_body(f: Feature) -> str:
    lines = [f"**{TRACKS[f.track]} · Phase {f.phase} · {f.points} pts**", ""]
    lines += [("✅ " if done else "⬜ ") + text for done, text in f.items]
    lines += ["", f"Plan: `{f.source}`",
              "Reply here with progress. Change the status tag when it moves."]
    return clip("\n".join(lines))


def chunks(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split long text into messages Discord accepts, breaking between lines."""
    out, cur = [], ""
    for line in text.split("\n"):
        line = clip(line, limit)
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


# --- the forum ------------------------------------------------------------------------------

def wanted_tags() -> list[dict]:
    names = STATUSES + list(TRACKS.values()) + [f"Phase {n}" for n in range(1, 5)] + KINDS
    return [{"name": n, **({"emoji_name": EMOJI[n]} if n in EMOJI else {})} for n in names]


def find_forum(dc: Discord, guild: str, name: str) -> dict | None:
    for ch in dc.call("GET", f"/guilds/{guild}/channels"):
        if ch["type"] == FORUM_TYPE and ch["name"] == name:
            return ch
    return None


def require_forum(dc: Discord, guild: str, name: str) -> dict:
    forum = find_forum(dc, guild, name)
    if forum is None:
        raise DiscordError(f"no forum named #{name} yet: run `sprint_board.py setup` first")
    return forum


def setup(dc: Discord, guild: str, name: str) -> tuple[dict, str]:
    """Create the forum with every tag, or add the tags an existing forum is missing."""
    forum = find_forum(dc, guild, name)
    if forum is None:
        forum = dc.call("POST", f"/guilds/{guild}/channels", {
            "name": name, "type": FORUM_TYPE, "topic": TOPIC, "available_tags": wanted_tags()})
        return forum, f"created #{name}"
    have = {t["name"] for t in forum.get("available_tags", [])}
    missing = [t for t in wanted_tags() if t["name"] not in have]
    if not missing:
        return forum, f"#{name} already set up"
    # Existing tags go back with their ids, or Discord would delete them.
    forum = dc.call("PATCH", f"/channels/{forum['id']}",
                    {"available_tags": forum.get("available_tags", []) + missing})
    return forum, f"added {len(missing)} tags to #{name}"


def tag_ids(forum: dict) -> dict[str, str]:
    return {t["name"]: t["id"] for t in forum["available_tags"]}


def tag_names(forum: dict) -> dict[str, str]:
    return {t["id"]: t["name"] for t in forum["available_tags"]}


def forum_posts(dc: Discord, guild: str, forum: dict) -> list[dict]:
    """Every post in the forum: the open ones plus the archived ones, page by page."""
    active = dc.call("GET", f"/guilds/{guild}/threads/active")["threads"]
    posts = [t for t in active if t.get("parent_id") == forum["id"]]
    before = None
    while True:
        query = "?limit=100" + (f"&before={urllib.parse.quote(before)}" if before else "")
        page = dc.call("GET", f"/channels/{forum['id']}/threads/archived/public{query}")
        posts += page["threads"]
        if not page.get("has_more") or not page["threads"]:
            return posts
        before = page["threads"][-1]["thread_metadata"]["archive_timestamp"]


def new_post(dc: Discord, forum: dict, name: str, content: str, tags: list[str]) -> dict:
    ids = tag_ids(forum)
    parts = chunks(content)
    post = dc.call("POST", f"/channels/{forum['id']}/threads", {
        "name": name[:100], "applied_tags": [ids[t] for t in tags],
        "message": {"content": parts[0], "allowed_mentions": NO_PINGS}})
    for part in parts[1:]:
        reply(dc, post["id"], part)
    return post


def reply(dc: Discord, channel_id: str, content: str) -> None:
    for part in chunks(content):
        dc.call("POST", f"/channels/{channel_id}/messages",
                {"content": part, "allowed_mentions": NO_PINGS})


def seed(dc: Discord | None, guild: str, forum: dict | None, features: list[Feature],
         apply: bool) -> list[str]:
    """One post per feature. Features already on the board (same #number) are skipped."""
    posted = set()
    if dc is not None and forum is not None:
        for p in forum_posts(dc, guild, forum):
            m = re.match(r"#(\d+) ", p["name"])
            if m:
                posted.add(int(m.group(1)))
    out = []
    for f in features:
        if f.number in posted:
            out.append(f"skip         {f.name} (already on the board)")
            continue
        if apply:
            new_post(dc, forum, f.name, feature_body(f),
                     [f.status, TRACKS[f.track], f"Phase {f.phase}"])
        out.append(f"{'posted' if apply else 'would post':<12} {f.name} [{TRACKS[f.track]}, {f.status}]")
    return out


def move(dc: Discord, forum: dict, post_id: str, status: str) -> str:
    """Swap a post's status tag, keeping its track and phase tags."""
    thread = dc.call("GET", f"/channels/{post_id}")
    names = tag_names(forum)
    keep = [t for t in thread.get("applied_tags", []) if names.get(t) not in STATUSES]
    body = {"applied_tags": keep + [tag_ids(forum)[status]]}
    if thread.get("thread_metadata", {}).get("archived"):
        body["archived"] = False  # an archived post can't be edited until it is reopened
    dc.call("PATCH", f"/channels/{post_id}", body)
    return f"{thread['name']} -> {status}"


def standup(dc: Discord, guild: str, forum: dict, today: date) -> dict:
    """Open today's standup post, or return it if it is already open."""
    title = f"Standup — {today:%a %b} {today.day}"
    for p in forum_posts(dc, guild, forum):
        if p["name"] == title:
            return p
    body = ("Reply with three lines:\n**Did:** …\n**Doing:** …\n**Blocked:** … (or none)\n"
            "Name the post or PR you mean. The Monday digest reads these replies.")
    return new_post(dc, forum, title, body, ["Standup"])


# --- the report ----------------------------------------------------------------------------

@dataclass
class Post:
    id: str
    name: str
    tags: list[str]
    created: datetime
    last: datetime
    replies: int
    recent: list[dict] = field(default_factory=list)  # {"author", "at", "text"}

    def _first(self, options) -> str | None:
        return next((t for t in self.tags if t in options), None)

    @property
    def status(self) -> str:
        return self._first(STATUSES) or "Backlog"

    @property
    def track(self) -> str | None:
        return self._first(TRACKS.values())

    @property
    def phase(self) -> int | None:
        tag = self._first([f"Phase {n}" for n in range(1, 5)])
        return int(tag.split()[1]) if tag else None

    @property
    def kind(self) -> str | None:
        return self._first(KINDS)

    @property
    def points(self) -> int:
        m = re.search(r"\((\d+) pts\)$", self.name)
        return int(m.group(1)) if m else 0


def collect(dc: Discord, guild: str, forum: dict, since: datetime | None) -> list[Post]:
    """Read every post; for those active since `since`, also read the people's replies."""
    names = tag_names(forum)
    posts = []
    for t in forum_posts(dc, guild, forum):
        last = snowflake_time(t.get("last_message_id") or t["id"])
        post = Post(t["id"], t["name"], [names[i] for i in t.get("applied_tags", []) if i in names],
                    snowflake_time(t["id"]), last, t.get("message_count", 0))
        if since is not None and last >= since:
            msgs = dc.call("GET", f"/channels/{t['id']}/messages?limit=50&after={snowflake_at(since)}")
            for m in sorted(msgs, key=lambda m: int(m["id"])):
                if m["id"] == t["id"] or m["author"].get("bot"):
                    continue  # the post's own opening message, or a bot's
                author = m["author"].get("global_name") or m["author"]["username"]
                post.recent.append({"author": author, "at": snowflake_time(m["id"]).isoformat(),
                                    "text": clip(m.get("content", ""), 500)})
        posts.append(post)
    return posts


def render(posts: list[Post], phase: int, span: tuple[date, date], now: datetime) -> str:
    """The board as Discord markdown (Discord has no tables, so lists only)."""
    feats = [p for p in posts if p.kind is None and p.phase == phase]
    total = sum(p.points for p in feats)
    pts = {s: sum(p.points for p in feats if p.status == s) for s in STATUSES}
    days_left = (span[1] - team_date(now)).days
    pct = round(100 * pts["Done"] / total) if total else 0
    lines = [
        f"**Phase {phase} · {span[0]:%b} {span[0].day} – {span[1]:%b} {span[1].day} · "
        f"{max(days_left, 0)} days left**",
        f"Points done {pts['Done']} / {total} ({pct}%) · review {pts['Review']} · "
        f"doing {pts['Doing']} · blocked {pts['Blocked']}",
        "", "**By track**",
    ]
    for track in TRACKS.values():
        mine = [p for p in feats if p.track == track]
        if not mine:
            continue
        counts = " · ".join(f"{sum(p.status == s for p in mine)} {s.lower()}"
                            for s in STATUSES if any(p.status == s for p in mine))
        lines.append(f"- {track}: {counts}")
    for status in ["Blocked", "Doing", "Review", "Done"]:
        group = [p for p in feats if p.status == status]
        if not group:
            continue
        lines += ["", f"**{EMOJI[status]} {status}**"]
        for p in sorted(group, key=lambda p: p.last):
            quiet = (now - p.last).days
            flag = " ⚠️ stale" if status == "Doing" and quiet >= STALE_DAYS else ""
            lines.append(f"- {p.name} · {p.track or 'no track'} · quiet {quiet} d{flag}")
    backlog = [p for p in feats if p.status == "Backlog"]
    if backlog:
        lines += ["", f"**{EMOJI['Backlog']} Backlog:** {len(backlog)} posts, "
                      f"{pts['Backlog']} pts not started"]
    return "\n".join(lines)


def report_json(posts: list[Post], phase: int, span: tuple[date, date], now: datetime) -> str:
    rows = []
    for p in posts:
        row = asdict(p)
        row.update(created=p.created.isoformat(), last=p.last.isoformat(), status=p.status,
                   track=p.track, phase=p.phase, kind=p.kind, points=p.points)
        rows.append(row)
    return json.dumps({"phase": phase, "phase_start": span[0].isoformat(),
                       "phase_end": span[1].isoformat(), "now": now.isoformat(),
                       "posts": rows}, ensure_ascii=False, indent=2)


# --- command line --------------------------------------------------------------------------

def main(argv: list[str] | None = None, dc: Discord | None = None,
         now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="The team's sprint board as a Discord forum.")
    ap.add_argument("--forum", default=os.environ.get("SPRINT_FORUM", "sprint-board"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="create the forum and its tags")
    s = sub.add_parser("seed", help="one post per feature in a phase")
    s.add_argument("--phase", type=int)
    s.add_argument("--apply", action="store_true", help="really post (default: dry run)")
    r = sub.add_parser("report", help="the board, plus recent replies")
    r.add_argument("--since", type=float, default=7, help="days of replies to read (default 7)")
    r.add_argument("--phase", type=int)
    r.add_argument("--json", action="store_true")
    sub.add_parser("standup", help="open today's standup post")
    p = sub.add_parser("post", help="post a file's text")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--title", help="start a new post with this title")
    where.add_argument("--into", help="reply inside the post with this id")
    p.add_argument("--file", required=True)
    p.add_argument("--tag", choices=KINDS, default="Digest")
    m = sub.add_parser("move", help="change a post's status tag")
    m.add_argument("post_id")
    m.add_argument("status", choices=STATUSES)
    args = ap.parse_args(argv)

    now = now or datetime.now(timezone.utc)
    cal = phase_calendar(now.year)
    phase = getattr(args, "phase", None) or current_phase(cal, team_date(now))

    guild = os.environ.get("SPRINT_GUILD_ID", "")
    if dc is None:
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if args.cmd == "seed" and not args.apply and not (token and guild):
            print("\n".join(seed(None, guild, None, all_features(phase), apply=False)))
            return 0
        if not (token and guild):
            print("Set DISCORD_BOT_TOKEN and SPRINT_GUILD_ID as environment secrets "
                  "(never in the repo). See docs/SPRINT_BOARD.md.", file=sys.stderr)
            return 2
        dc = Discord(token)

    try:
        if args.cmd == "setup":
            print(setup(dc, guild, args.forum)[1])
            return 0
        forum = require_forum(dc, guild, args.forum)
        if args.cmd == "seed":
            print("\n".join(seed(dc, guild, forum, all_features(phase), args.apply)))
        elif args.cmd == "report":
            posts = collect(dc, guild, forum, now - timedelta(days=args.since))
            out = report_json if args.json else render
            print(out(posts, phase, cal[phase], now))
        elif args.cmd == "standup":
            post = standup(dc, guild, forum, team_date(now))
            print(json.dumps({"id": post["id"], "name": post["name"]}))
        elif args.cmd == "post":
            text = Path(args.file).read_text(encoding="utf-8")
            if args.into:
                reply(dc, args.into, text)
                print(f"replied in {args.into}")
            else:
                post = new_post(dc, forum, args.title, text, [args.tag])
                print(json.dumps({"id": post["id"], "name": post["name"]}))
        elif args.cmd == "move":
            print(move(dc, forum, args.post_id, args.status))
    except DiscordError as err:
        print(f"discord: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
