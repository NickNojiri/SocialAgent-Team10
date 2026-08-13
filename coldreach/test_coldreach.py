"""Offline tests. No network, no Ollama, no mail server required.

    python -m pytest test_coldreach.py -q
"""

from __future__ import annotations

import email
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("CR_EMAIL", "student@example.com")
os.environ.setdefault("CR_EMAIL_PASSWORD", "app-password")
os.environ.setdefault("CR_FULL_NAME", "Jane Doe")
os.environ.setdefault("CR_TIMEZONE", "America/Los_Angeles")

import coldreach as cr


@pytest.fixture()
def cfg(tmp_path: Path) -> cr.Config:
    base = cr.Config.load()
    return cr.dataclasses.replace(
        base, db_path=tmp_path / "test.db", ics_dir=tmp_path / "invites"
    )


# --- text handling ---------------------------------------------------------

def test_strip_thinking_removes_reasoning_block():
    assert cr.strip_thinking("<think>hmm</think>Hello") == "Hello"
    assert cr.strip_thinking("<think>unterminated").strip() == ""


def test_sanitise_body_drops_greeting_and_signoff():
    raw = "Dear Professor Smith,\n\nI read about your work.\n\nBest,\nBob"
    out = cr._sanitise_body(raw, 100)
    assert "Dear Professor" not in out
    assert "Bob" not in out
    assert "I read about your work." in out


def test_sanitise_body_enforces_word_cap():
    out = cr._sanitise_body(" ".join(["word"] * 300), 100)
    assert len(out.split()) <= 100


def test_compose_email_adds_greeting_signoff_and_optout():
    cfg = cr.Config.load()
    out = cr.compose_email(cfg, "Ada Lovelace", "Body text here.")
    assert out.startswith("Dear Professor Lovelace,")
    assert "Body text here." in out
    assert "no thanks" in out
    assert "Jane Doe" in out


def test_clean_line_strips_subject_prefix_and_quotes():
    assert cr._clean_line('"Subject: A question"') == "A question"


def test_strip_quotes_removes_history():
    reply = (
        "Happy to chat next week.\n\n"
        "On Mon, 3 Feb 2025, Jane Doe wrote:\n"
        "> my original cold email\n"
    )
    assert cr.strip_quotes(reply) == "Happy to chat next week."


def test_extract_body_prefers_plain_text():
    msg = email.message_from_string(
        "Content-Type: multipart/alternative; boundary=b\n\n"
        "--b\nContent-Type: text/plain\n\nplain version\n"
        "--b\nContent-Type: text/html\n\n<p>html version</p>\n--b--\n"
    )
    assert "plain version" in cr.extract_body(msg)


def test_extract_body_falls_back_to_html():
    msg = email.message_from_string(
        "Content-Type: text/html\n\n<p>Hello <b>there</b></p>"
    )
    body = cr.extract_body(msg)
    assert "Hello" in body and "<p>" not in body


def test_is_auto_reply_detects_ooo_headers():
    ooo = email.message_from_string("Auto-Submitted: auto-replied\n\nAway.")
    human = email.message_from_string("Subject: Re: hi\n\nSure.")
    assert cr.is_auto_reply(ooo)
    assert not cr.is_auto_reply(human)


# --- classification fallback ----------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("I would be happy to chat, send me a time.", "Positive"),
        ("Please do not contact me again.", "Hard No"),
        ("I have no openings at this time.", "Soft No"),
        ("I am out of the office until March.", "OOO"),
        ("What year are you in?", "Question"),
    ],
)
def test_heuristic_intent(text, expected):
    intent, confidence = cr.heuristic_intent(text)
    assert intent == expected
    assert 0 <= confidence <= 1


# --- scheduling ------------------------------------------------------------

def test_next_free_slot_is_a_weekday_in_allowed_hours(cfg):
    start, end = cr.next_free_slot(cfg, [])
    local = start.astimezone(cfg.tz)
    assert local.weekday() < 5
    assert local.hour in cfg.meeting_hours
    assert end - start == timedelta(minutes=cfg.meeting_minutes)
    assert start >= datetime.now(timezone.utc) + timedelta(
        hours=cfg.lead_time_hours - 1
    )


def test_next_free_slot_skips_taken_slots(cfg):
    first, _ = cr.next_free_slot(cfg, [])
    second, _ = cr.next_free_slot(cfg, [first])
    assert second > first


def test_build_ics_is_a_valid_request(cfg):
    start, end = cr.next_free_slot(cfg, [])
    ics = cr.build_ics(cfg, "Ada Lovelace", "ada@example.edu", start, end, "uid-1")
    text = ics.decode()
    assert "BEGIN:VCALENDAR" in text and "END:VEVENT" in text
    assert "METHOD:REQUEST" in text
    assert "UID:uid-1" in text
    assert "ada@example.edu" in text
    assert "ORGANIZER" in text

    from icalendar import Calendar

    event = Calendar.from_ical(ics).walk("VEVENT")[0]
    assert event["dtstart"].dt == start
    assert event["dtend"].dt == end


# --- database --------------------------------------------------------------

def test_init_creates_tables_and_is_idempotent(cfg):
    cr.init_db(cfg)
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"professors", "inbound", "meetings", "suppression", "events"} <= names


def test_add_professor_rejects_duplicate_email(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        assert cr.add_professor(conn, "Ada", "ada@example.edu", "engines")
        assert not cr.add_professor(conn, "Ada Again", "ADA@example.edu", "engines")


def test_import_csv_reads_headers_case_insensitively(cfg, tmp_path):
    csv_path = tmp_path / "c.csv"
    csv_path.write_text(
        "name,email,topic\nAda,ada@example.edu,engines\nbad,not-an-email,x\n"
    )
    cr.init_db(cfg)
    added, skipped = cr.import_csv(cfg, csv_path)
    assert (added, skipped) == (1, 1)


def test_approve_moves_drafted_to_approved(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.add_professor(conn, "Ada", "ada@example.edu", "engines")
        cr.set_status(conn, 1, cr.STATUS_DRAFTED, draft_subject="s", draft_body="b")
    assert cr.approve(cfg, [1]) == 1
    assert cr.list_by_status(cfg, cr.STATUS_APPROVED)[0]["email"] == "ada@example.edu"


def test_suppression_blocks_repeat_contact(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.suppress(conn, "ada@example.edu", "hard no")
        assert cr.is_suppressed(conn, "ADA@example.edu")
        assert not cr.is_suppressed(conn, "grace@example.edu")


def test_status_counts_groups_by_status(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.add_professor(conn, "Ada", "ada@example.edu", "engines")
        cr.add_professor(conn, "Grace", "grace@example.edu", "compilers")
        cr.set_status(conn, 1, cr.STATUS_SENT)
    assert dict(cr.status_counts(cfg)) == {cr.STATUS_SENT: 1, cr.STATUS_PENDING: 1}


# --- drafting with a stubbed model ----------------------------------------

class FakeBrain:
    """Stands in for Ollama so the draft path is testable offline."""

    def __init__(self, body: str, subject: str = "Question about your research"):
        self.body, self.subject = body, subject

    def generate(self, system, prompt, **kwargs):
        return self.subject if "subject line" in prompt.lower() else self.body


def test_draft_pending_marks_rows_drafted(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.add_professor(conn, "Ada Lovelace", "ada@example.edu", "analytical engines")

    body = (
        "Your work on analytical engines is the reason I started reading about "
        "symbolic computation, and I have been following the area since. I am "
        "hoping to understand how you choose problems in this space."
    )
    assert cr.draft_pending(cfg, FakeBrain(body)) == 1

    row = cr.list_by_status(cfg, cr.STATUS_DRAFTED)[0]
    assert row["draft_subject"] == "Question about your research"
    assert row["draft_body"].startswith("Dear Professor Lovelace,")
    assert row["drafted_at"]


def test_draft_pending_rejects_too_short_output(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.add_professor(conn, "Ada", "ada@example.edu", "engines")
    assert cr.draft_pending(cfg, FakeBrain("Too short.")) == 0
    assert cr.list_by_status(cfg, cr.STATUS_PENDING)


def test_draft_pending_skips_suppressed_contacts(cfg):
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.add_professor(conn, "Ada", "ada@example.edu", "engines")
        cr.suppress(conn, "ada@example.edu", "hard no")
    assert cr.draft_pending(cfg, FakeBrain("x " * 60)) == 0
    assert cr.list_by_status(cfg, cr.STATUS_HARD_NO)


# --- message construction --------------------------------------------------

def _reply(from_addr: str, body: str, message_id: str, extra: str = "") -> email.message.Message:
    return email.message_from_string(
        f"From: Prof <{from_addr}>\nSubject: Re: your email\n"
        f"Message-ID: {message_id}\nDate: Mon, 3 Feb 2025 10:00:00 +0000\n"
        f"{extra}Content-Type: text/plain\n\n{body}\n"
    )


class StubBrain:
    def __init__(self, intent: str):
        self.intent = intent

    def generate(self, system, prompt, **kwargs):
        return f'{{"intent": "{self.intent}", "confidence": 0.9}}'


@pytest.fixture()
def contacted(cfg):
    """A professor who has already been emailed."""
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        cr.add_professor(conn, "Ada Lovelace", "ada@example.edu", "engines")
        cr.set_status(conn, 1, cr.STATUS_SENT, sent_at="2025-02-01T10:00:00+00:00")
    return cfg


def _run_poll(cfg, monkeypatch, messages, intent, sent_box=None):
    monkeypatch.setattr(cr, "fetch_recent", lambda c, days=30: messages)
    if sent_box is not None:
        class _Server:
            def send_message(self, msg):
                sent_box.append(msg)

        import contextlib

        @contextlib.contextmanager
        def _session(c):
            yield _Server()

        monkeypatch.setattr(cr, "smtp_session", _session)
    return cr.poll_replies(cfg, StubBrain(intent))


def test_poll_positive_reply_schedules_and_invites(contacted, monkeypatch):
    sent: list = []
    counts = _run_poll(
        contacted,
        monkeypatch,
        [_reply("ada@example.edu", "Happy to talk, send a time.", "<r1@x>")],
        "Positive",
        sent,
    )
    assert counts["new"] == 1 and counts["scheduled"] == 1
    assert cr.list_by_status(contacted, cr.STATUS_SCHEDULED)[0]["email"] == "ada@example.edu"

    invite = sent[0]
    assert invite["In-Reply-To"] == "<r1@x>"
    assert invite["Subject"].startswith("Re:")
    types = {p.get_content_type() for p in invite.walk()}
    assert "text/calendar" in types

    assert len(cr.upcoming_meetings(contacted)) == 1


def test_poll_hard_no_suppresses_and_sends_nothing(contacted, monkeypatch):
    sent: list = []
    _run_poll(
        contacted,
        monkeypatch,
        [_reply("ada@example.edu", "Please do not contact me again.", "<r2@x>")],
        "Hard No",
        sent,
    )
    assert sent == []
    assert cr.list_by_status(contacted, cr.STATUS_HARD_NO)
    with cr.connect(contacted) as conn:
        assert cr.is_suppressed(conn, "ada@example.edu")


def test_poll_ooo_leaves_status_untouched(contacted, monkeypatch):
    _run_poll(
        contacted,
        monkeypatch,
        [
            _reply(
                "ada@example.edu",
                "I am away.",
                "<r3@x>",
                extra="Auto-Submitted: auto-replied\n",
            )
        ],
        "Positive",  # header detection must win over the model
    )
    assert cr.list_by_status(contacted, cr.STATUS_SENT)
    with cr.connect(contacted) as conn:
        assert conn.execute("SELECT intent FROM inbound").fetchone()["intent"] == "OOO"


def test_poll_ignores_mail_from_strangers(contacted, monkeypatch):
    counts = _run_poll(
        contacted,
        monkeypatch,
        [_reply("spam@elsewhere.com", "Buy things.", "<r4@x>")],
        "Positive",
    )
    assert counts["new"] == 0


def test_poll_is_idempotent_on_reruns(contacted, monkeypatch):
    sent: list = []
    messages = [_reply("ada@example.edu", "Happy to talk.", "<r5@x>")]
    first = _run_poll(contacted, monkeypatch, messages, "Positive", sent)
    second = _run_poll(contacted, monkeypatch, messages, "Positive", sent)
    assert first["new"] == 1 and second["new"] == 0
    assert len(sent) == 1  # no duplicate invite on the second run


def test_poll_soft_no_marks_status(contacted, monkeypatch):
    _run_poll(
        contacted,
        monkeypatch,
        [_reply("ada@example.edu", "No openings this year.", "<r6@x>")],
        "Soft No",
    )
    assert cr.list_by_status(contacted, cr.STATUS_SOFT_NO)


def test_classify_falls_back_when_model_returns_junk(monkeypatch):
    class Junk:
        def generate(self, system, prompt, **kwargs):
            return "not json at all"

    intent, _ = cr.classify_reply(Junk(), "I would be happy to chat.")
    assert intent == "Positive"  # heuristic rescued it


def test_build_message_sets_threading_headers(cfg):
    msg = cr._build_message(
        cfg, "ada@example.edu", "Ada", "Re: hi", "body", in_reply_to="<abc@x>"
    )
    assert msg["In-Reply-To"] == "<abc@x>"
    assert msg["References"] == "<abc@x>"
    assert msg["List-Unsubscribe"]
    assert "ada@example.edu" in msg["To"]
