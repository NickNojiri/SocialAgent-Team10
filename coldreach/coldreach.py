"""ColdReach - a local-first cold-email agent for academic outreach.

Everything runs on your own machine: SQLite for state, smtplib/imaplib for
transport, Ollama for the language model, icalendar for invites. No SaaS, no
cloud APIs, no GPU required.

This module is the engine. `cli.py` is the command-line front end.
"""

from __future__ import annotations

import dataclasses
import email
import email.utils
import imaplib
import json
import os
import re
import smtplib
import sqlite3
import ssl
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence
from zoneinfo import ZoneInfo

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so we avoid a python-dotenv dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "").lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # Identity
    full_name: str
    email_address: str
    signature: str
    role_blurb: str

    # SMTP
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str

    # IMAP
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str

    # Ollama
    ollama_host: str
    ollama_model: str
    ollama_keep_alive: str
    ollama_num_ctx: int
    ollama_timeout: int

    # Behaviour
    db_path: Path
    ics_dir: Path
    timezone: str
    max_sends_per_run: int
    send_delay_seconds: int
    meeting_minutes: int
    lead_time_hours: int
    meeting_hours: tuple[int, ...]
    max_draft_words: int
    net_timeout: int
    dry_run: bool

    @classmethod
    def load(cls) -> "Config":
        smtp_user = _env("CR_SMTP_USER") or _env("CR_EMAIL")
        imap_user = _env("CR_IMAP_USER") or smtp_user
        password = _env("CR_EMAIL_PASSWORD")
        hours = _env("CR_MEETING_HOURS", "10,11,14,15,16")
        try:
            meeting_hours = tuple(
                sorted({int(h) for h in hours.split(",") if h.strip()})
            )
        except ValueError:
            meeting_hours = (10, 11, 14, 15, 16)

        return cls(
            full_name=_env("CR_FULL_NAME", "A Prospective Student"),
            email_address=_env("CR_EMAIL") or smtp_user,
            signature=_env("CR_SIGNATURE", ""),
            role_blurb=_env(
                "CR_ROLE_BLURB",
                "an undergraduate looking for research experience",
            ),
            smtp_host=_env("CR_SMTP_HOST", "smtp.gmail.com"),
            smtp_port=_env_int("CR_SMTP_PORT", 587),
            smtp_user=smtp_user,
            smtp_password=_env("CR_SMTP_PASSWORD") or password,
            imap_host=_env("CR_IMAP_HOST", "imap.gmail.com"),
            imap_port=_env_int("CR_IMAP_PORT", 993),
            imap_user=imap_user,
            imap_password=_env("CR_IMAP_PASSWORD") or password,
            imap_folder=_env("CR_IMAP_FOLDER", "INBOX"),
            ollama_host=_env("CR_OLLAMA_HOST", "http://127.0.0.1:11434"),
            ollama_model=_env("CR_OLLAMA_MODEL", "qwen2.5:3b"),
            ollama_keep_alive=_env("CR_OLLAMA_KEEP_ALIVE", "0"),
            ollama_num_ctx=_env_int("CR_OLLAMA_NUM_CTX", 2048),
            ollama_timeout=_env_int("CR_OLLAMA_TIMEOUT", 180),
            db_path=Path(_env("CR_DB_PATH", str(ROOT / "professors.db"))),
            ics_dir=Path(_env("CR_ICS_DIR", str(ROOT / "invites"))),
            timezone=_env("CR_TIMEZONE", "America/Los_Angeles"),
            max_sends_per_run=_env_int("CR_MAX_SENDS_PER_RUN", 15),
            send_delay_seconds=_env_int("CR_SEND_DELAY_SECONDS", 45),
            meeting_minutes=_env_int("CR_MEETING_MINUTES", 15),
            lead_time_hours=_env_int("CR_LEAD_TIME_HOURS", 48),
            meeting_hours=meeting_hours,
            max_draft_words=_env_int("CR_MAX_DRAFT_WORDS", 100),
            net_timeout=_env_int("CR_NET_TIMEOUT", 30),
            dry_run=_env_bool("CR_DRY_RUN", False),
        )

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")

    def require_email(self) -> None:
        missing = [
            name
            for name, value in (
                ("CR_EMAIL", self.email_address),
                ("CR_EMAIL_PASSWORD", self.smtp_password),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(missing)
            )


def replace_dry_run(cfg: Config, dry_run: bool) -> Config:
    """Return a copy of the config with dry_run flipped (Config is frozen)."""
    return dataclasses.replace(cfg, dry_run=dry_run)


class ConfigError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Logging (stdout, cron-friendly)
# --------------------------------------------------------------------------

def log(message: str, *, level: str = "INFO") -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stream = sys.stderr if level in {"WARN", "ERROR"} else sys.stdout
    print(f"{stamp} [{level}] {message}", file=stream, flush=True)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

STATUS_PENDING = "Pending"
STATUS_DRAFTED = "Drafted"
STATUS_APPROVED = "Approved"
STATUS_SENT = "Sent"
STATUS_REPLIED = "Replied"
STATUS_SCHEDULED = "Scheduled"
STATUS_SOFT_NO = "Soft No"
STATUS_HARD_NO = "Hard No"
STATUS_FAILED = "Failed"

ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_DRAFTED,
    STATUS_APPROVED,
    STATUS_SENT,
    STATUS_REPLIED,
    STATUS_SCHEDULED,
    STATUS_SOFT_NO,
    STATUS_HARD_NO,
    STATUS_FAILED,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS professors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    research_topic  TEXT NOT NULL DEFAULT '',
    institution     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'Pending',
    draft_subject   TEXT,
    draft_body      TEXT,
    drafted_at      TEXT,
    sent_at         TEXT,
    sent_message_id TEXT,
    sent_subject    TEXT,
    replied_at      TEXT,
    last_intent     TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inbound (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL UNIQUE,
    professor_id INTEGER REFERENCES professors(id) ON DELETE SET NULL,
    from_addr    TEXT NOT NULL DEFAULT '',
    subject      TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL DEFAULT '',
    intent       TEXT NOT NULL DEFAULT '',
    confidence   REAL NOT NULL DEFAULT 0,
    snippet      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meetings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    professor_id INTEGER NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    uid          TEXT NOT NULL UNIQUE,
    start_utc    TEXT NOT NULL,
    end_utc      TEXT NOT NULL,
    ics_path     TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS suppression (
    email      TEXT PRIMARY KEY COLLATE NOCASE,
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL DEFAULT (datetime('now')),
    professor_id INTEGER,
    kind         TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_professors_status ON professors(status);
CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(start_utc);
CREATE INDEX IF NOT EXISTS idx_events_prof ON events(professor_id);
"""


@contextmanager
def connect(cfg: Config) -> Iterator[sqlite3.Connection]:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(cfg: Config) -> None:
    with connect(cfg) as conn:
        conn.executescript(SCHEMA)
    log(f"Database ready at {cfg.db_path}")


def record_event(
    conn: sqlite3.Connection, professor_id: Optional[int], kind: str, detail: str = ""
) -> None:
    conn.execute(
        "INSERT INTO events (professor_id, kind, detail) VALUES (?, ?, ?)",
        (professor_id, kind, detail[:500]),
    )


def set_status(
    conn: sqlite3.Connection, professor_id: int, status: str, **fields: object
) -> None:
    assignments = ["status = ?", "updated_at = datetime('now')"]
    values: list[object] = [status]
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.append(professor_id)
    conn.execute(
        f"UPDATE professors SET {', '.join(assignments)} WHERE id = ?", values
    )


def is_suppressed(conn: sqlite3.Connection, address: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppression WHERE email = ?", (address,)
    ).fetchone()
    return row is not None


def suppress(conn: sqlite3.Connection, address: str, reason: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO suppression (email, reason) VALUES (?, ?)",
        (address, reason),
    )


def add_professor(
    conn: sqlite3.Connection,
    name: str,
    address: str,
    topic: str,
    institution: str = "",
) -> bool:
    """Insert one contact. Returns False when the email already exists."""
    try:
        conn.execute(
            "INSERT INTO professors (name, email, research_topic, institution)"
            " VALUES (?, ?, ?, ?)",
            (name.strip(), address.strip().lower(), topic.strip(), institution.strip()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def import_csv(cfg: Config, csv_path: Path) -> tuple[int, int]:
    """Load contacts from a CSV with Name, Email, Research_Topic[, Institution]."""
    import csv as _csv

    added = skipped = 0
    with connect(cfg) as conn, csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        lowered = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}

        def pick(*names: str) -> Optional[str]:
            for candidate in names:
                if candidate in lowered:
                    return lowered[candidate]
            return None

        col_name = pick("name", "professor", "full_name")
        col_email = pick("email", "email_address")
        col_topic = pick("research_topic", "topic", "research", "interest")
        col_inst = pick("institution", "university", "school", "department")
        if not col_name or not col_email:
            raise ConfigError("CSV needs at least 'Name' and 'Email' columns")

        for row in reader:
            address = (row.get(col_email) or "").strip()
            if not address or "@" not in address:
                skipped += 1
                continue
            ok = add_professor(
                conn,
                (row.get(col_name) or "").strip(),
                address,
                (row.get(col_topic) or "").strip() if col_topic else "",
                (row.get(col_inst) or "").strip() if col_inst else "",
            )
            added += int(ok)
            skipped += int(not ok)
    return added, skipped


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------

class Brain:
    """Thin wrapper over the Ollama client.

    `keep_alive="0"` unloads the model the moment the call returns, which keeps
    the resident footprint near zero between cron runs. Set CR_OLLAMA_KEEP_ALIVE
    to something like "5m" if you would rather trade RAM for latency.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import ollama
            except ImportError as exc:  # pragma: no cover - import guard
                raise LLMError(
                    "The 'ollama' package is missing. Run: pip install ollama"
                ) from exc
            self._client = ollama.Client(
                host=self.cfg.ollama_host, timeout=self.cfg.ollama_timeout
            )
        return self._client

    def available(self) -> bool:
        try:
            self.client.list()
            return True
        except Exception as exc:
            log(f"Ollama unreachable at {self.cfg.ollama_host}: {exc}", level="WARN")
            return False

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        json_mode: bool = False,
        max_tokens: int = 320,
        temperature: float = 0.4,
    ) -> str:
        options = {
            "num_ctx": self.cfg.ollama_num_ctx,
            "num_predict": max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
        }
        kwargs = {
            "model": self.cfg.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": options,
            "keep_alive": self.cfg.ollama_keep_alive,
        }
        if json_mode:
            kwargs["format"] = "json"
        try:
            response = self.client.chat(**kwargs)
        except Exception as exc:
            raise LLMError(f"Ollama call failed: {exc}") from exc
        content = (response.get("message") or {}).get("content", "")
        return strip_thinking(content).strip()


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Reasoning-tuned small models (qwen3) emit <think> blocks. Drop them."""
    text = THINK_RE.sub("", text)
    # An unterminated block means the model ran out of tokens mid-thought.
    if "<think>" in text.lower():
        text = re.split(r"<think>", text, flags=re.IGNORECASE)[0]
    return text.replace("</think>", "")


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------

DRAFT_SYSTEM = (
    "You write short, plain cold emails from a student to a professor. "
    "You are concise and specific. You never invent facts, credentials, "
    "publication titles, or shared history. You never use marketing language, "
    "em dashes, or exclamation marks. Output the email body only: no subject "
    "line, no greeting placeholders like [Name], no sign-off, no commentary."
)

DRAFT_TEMPLATE = """\
Write the body of a cold email.

From: {sender}, {role}
To: Professor {name}{institution}
Their research area: {topic}

Rules:
- Under {max_words} words. Three short paragraphs at most.
- Open by naming their research area specifically and why it interests you.
- State one concrete ask: a 15 minute conversation about their work.
- No greeting line and no sign-off; those are added separately.
- Do not claim to have read a specific paper.
"""

SUBJECT_TEMPLATE = (
    "Write one email subject line for a student asking Professor {name} for a "
    "15 minute conversation about their work on {topic}. Under 9 words, plain "
    "sentence case, no quotes, no colon-heavy hype, no emoji. Output the "
    "subject line and nothing else."
)


def _clean_line(text: str) -> str:
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"^(subject|re)\s*:\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _clamp_words(text: str, limit: int) -> str:
    """Trim to `limit` words on a sentence boundary where possible."""
    words = text.split()
    if len(words) <= limit:
        return text
    truncated = " ".join(words[:limit])
    cut = max(truncated.rfind("."), truncated.rfind("?"))
    return truncated[: cut + 1] if cut > 40 else truncated.rstrip(",;: ") + "."


def _sanitise_body(text: str, limit: int) -> str:
    """Strip the artefacts small models like to add around a draft."""
    text = strip_thinking(text)
    text = re.sub(r"^\s*(subject|body|email)\s*:.*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^\s*(dear|hi|hello|greetings)[^\n]*\n", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\n\s*(best|sincerely|regards|thanks|thank you|kind regards|warmly)[^\n]*"
        r"(\n.*)*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("—", ", ").replace("[Name]", "").replace("**", "")
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n")]
    text = "\n\n".join(p for p in paragraphs if p)
    return _clamp_words(text.strip(), limit)


def compose_email(cfg: Config, name: str, body: str) -> str:
    """Wrap a generated body in a deterministic greeting, sign-off and opt-out."""
    last_name = name.split()[-1] if name.strip() else "there"
    greeting = f"Dear Professor {last_name},"
    signature = cfg.signature.strip() or f"Best,\n{cfg.full_name}"
    optout = "If this is not welcome, reply 'no thanks' and I will not write again."
    return f"{greeting}\n\n{body}\n\n{optout}\n\n{signature}\n"


def draft_pending(cfg: Config, brain: Brain, limit: int = 25) -> int:
    """Phase 1: turn Pending rows into reviewable Drafted rows."""
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT * FROM professors WHERE status = ? ORDER BY id LIMIT ?",
            (STATUS_PENDING, limit),
        ).fetchall()

        if not rows:
            log("No pending contacts to draft.")
            return 0

        drafted = 0
        for row in rows:
            if is_suppressed(conn, row["email"]):
                set_status(conn, row["id"], STATUS_HARD_NO, notes="suppressed")
                continue

            topic = row["research_topic"] or "their current research"
            institution = f" at {row['institution']}" if row["institution"] else ""
            try:
                raw_body = brain.generate(
                    DRAFT_SYSTEM,
                    DRAFT_TEMPLATE.format(
                        sender=cfg.full_name,
                        role=cfg.role_blurb,
                        name=row["name"],
                        institution=institution,
                        topic=topic,
                        max_words=cfg.max_draft_words,
                    ),
                    max_tokens=320,
                    temperature=0.5,
                )
                raw_subject = brain.generate(
                    "You write plain, specific email subject lines.",
                    SUBJECT_TEMPLATE.format(name=row["name"], topic=topic),
                    max_tokens=40,
                    temperature=0.3,
                )
            except LLMError as exc:
                log(f"Draft failed for {row['email']}: {exc}", level="ERROR")
                record_event(conn, row["id"], "draft_failed", str(exc))
                continue

            body = _sanitise_body(raw_body, cfg.max_draft_words)
            if len(body.split()) < 20:
                log(
                    f"Model returned an unusably short draft for {row['email']}; "
                    "leaving as Pending.",
                    level="WARN",
                )
                record_event(conn, row["id"], "draft_rejected", body[:200])
                continue

            subject = _clean_line(raw_subject) or f"Question about your {topic} research"
            set_status(
                conn,
                row["id"],
                STATUS_DRAFTED,
                draft_subject=subject[:150],
                draft_body=compose_email(cfg, row["name"], body),
                drafted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            record_event(conn, row["id"], "drafted", subject[:150])
            drafted += 1
            log(f"Drafted -> {row['email']} ({len(body.split())} words)")

    return drafted


# --------------------------------------------------------------------------
# SMTP
# --------------------------------------------------------------------------

@contextmanager
def smtp_session(cfg: Config) -> Iterator[smtplib.SMTP]:
    cfg.require_email()
    context = ssl.create_default_context()
    if cfg.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            cfg.smtp_host, cfg.smtp_port, timeout=cfg.net_timeout, context=context
        )
    else:
        server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.net_timeout)
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
    try:
        server.login(cfg.smtp_user, cfg.smtp_password)
        yield server
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _build_message(
    cfg: Config,
    to_addr: str,
    to_name: str,
    subject: str,
    body: str,
    *,
    in_reply_to: Optional[str] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((cfg.full_name, cfg.email_address))
    msg["To"] = email.utils.formataddr((to_name, to_addr)) if to_name else to_addr
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=cfg.email_address.split("@")[-1])
    msg["List-Unsubscribe"] = f"<mailto:{cfg.email_address}?subject=unsubscribe>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


def send_approved(cfg: Config, limit: Optional[int] = None) -> int:
    """Phase 1b: send the drafts you approved. Never run this from cron."""
    cap = limit if limit is not None else cfg.max_sends_per_run
    sent = 0
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT * FROM professors WHERE status = ? ORDER BY id LIMIT ?",
            (STATUS_APPROVED, cap),
        ).fetchall()

        if not rows:
            log("Nothing approved to send. Use 'review' then 'approve'.")
            return 0

        if cfg.dry_run:
            for row in rows:
                log(f"[dry-run] would send to {row['email']}: {row['draft_subject']}")
            return 0

        with smtp_session(cfg) as server:
            for index, row in enumerate(rows):
                if is_suppressed(conn, row["email"]):
                    set_status(conn, row["id"], STATUS_HARD_NO, notes="suppressed")
                    continue
                msg = _build_message(
                    cfg,
                    row["email"],
                    row["name"],
                    row["draft_subject"] or "Quick question about your research",
                    row["draft_body"] or "",
                )
                try:
                    server.send_message(msg)
                except smtplib.SMTPException as exc:
                    log(f"Send failed for {row['email']}: {exc}", level="ERROR")
                    set_status(conn, row["id"], STATUS_FAILED, notes=str(exc)[:200])
                    record_event(conn, row["id"], "send_failed", str(exc))
                    continue

                set_status(
                    conn,
                    row["id"],
                    STATUS_SENT,
                    sent_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    sent_message_id=msg["Message-ID"],
                    sent_subject=msg["Subject"],
                )
                record_event(conn, row["id"], "sent", msg["Subject"])
                sent += 1
                log(f"Sent -> {row['email']}")
                conn.commit()

                # Spacing sends out matters more for deliverability than speed.
                if cfg.send_delay_seconds and index < len(rows) - 1:
                    time.sleep(cfg.send_delay_seconds)
    return sent


# --------------------------------------------------------------------------
# IMAP
# --------------------------------------------------------------------------

QUOTE_MARKERS = (
    re.compile(r"^\s*on .{0,120}wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*original message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*from:\s.+", re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*sent from my \w+", re.IGNORECASE),
)


def decode_field(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def extract_body(msg: email.message.Message) -> str:
    """Pull the plain-text part, falling back to de-tagged HTML."""
    text = ""
    html = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            decoded = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and not text:
            text = decoded
        elif part.get_content_type() == "text/html" and not html:
            html = decoded

    if not text and html:
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>", "\n", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;?", " ", text)
    return text


def strip_quotes(text: str) -> str:
    """Keep only what the human actually typed in this reply."""
    lines: list[str] = []
    for line in text.splitlines():
        if any(marker.match(line) for marker in QUOTE_MARKERS):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line.rstrip())
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or text.strip()


def is_auto_reply(msg: email.message.Message) -> bool:
    auto = (msg.get("Auto-Submitted") or "").lower()
    if auto and auto != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    precedence = (msg.get("Precedence") or "").lower()
    return precedence in {"bulk", "auto_reply", "junk"}


@contextmanager
def imap_session(cfg: Config) -> Iterator[imaplib.IMAP4_SSL]:
    cfg.require_email()
    # Without an explicit timeout imaplib will block forever on a dead network.
    conn = imaplib.IMAP4_SSL(
        cfg.imap_host,
        cfg.imap_port,
        ssl_context=ssl.create_default_context(),
        timeout=cfg.net_timeout,
    )
    try:
        conn.login(cfg.imap_user, cfg.imap_password)
        conn.select(cfg.imap_folder)
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


def fetch_recent(cfg: Config, days: int = 30) -> list[email.message.Message]:
    """Fetch recent mail with PEEK so we never flip the read flag on your inbox."""
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    messages: list[email.message.Message] = []
    with imap_session(cfg) as conn:
        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            log(f"IMAP search failed: {status}", level="WARN")
            return messages
        uids = (data[0] or b"").split()
        for uid in uids[-400:]:
            status, payload = conn.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not payload:
                continue
            for part in payload:
                if isinstance(part, tuple) and part[1]:
                    messages.append(email.message_from_bytes(part[1]))
                    break
    return messages


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

INTENTS = ("Positive", "Question", "Soft No", "Hard No", "OOO", "Other")

CLASSIFY_SYSTEM = (
    "You classify a professor's reply to a student's cold email. "
    "Answer with JSON only."
)

CLASSIFY_TEMPLATE = """\
Reply text:
\"\"\"
{body}
\"\"\"

Choose exactly one intent:
- "Positive": they agree to talk, meet, or ask you to pick a time.
- "Question": they engage but ask something before committing.
- "Soft No": not now, too busy, try again later, no current openings.
- "Hard No": clear refusal, not interested, asks you to stop writing.
- "OOO": automatic out-of-office or vacation autoresponder.
- "Other": anything else, including bounces and unrelated mail.

Respond with JSON exactly like:
{{"intent": "Positive", "confidence": 0.9}}
"""

POSITIVE_HINTS = (
    "happy to", "glad to", "would be glad", "sure", "let's meet", "lets meet",
    "let's talk", "lets talk", "send me a time", "set up a time", "schedule",
    "book a", "available on", "works for me", "sounds good", "yes,",
)
HARD_NO_HINTS = (
    "not interested", "do not contact", "don't contact", "stop emailing",
    "unsubscribe", "remove me", "no thanks", "please do not",
)
SOFT_NO_HINTS = (
    "not at this time", "no openings", "no positions", "unfortunately",
    "not accepting", "too busy", "check back", "later in the year", "next year",
)
OOO_HINTS = (
    "out of office", "out of the office", "on leave", "on sabbatical",
    "automatic reply", "auto-reply", "away until", "limited access to email",
)


def heuristic_intent(body: str) -> tuple[str, float]:
    """Deterministic fallback for when the model is down or returns junk."""
    low = body.lower()
    for hints, intent in (
        (OOO_HINTS, "OOO"),
        (HARD_NO_HINTS, "Hard No"),
        (SOFT_NO_HINTS, "Soft No"),
        (POSITIVE_HINTS, "Positive"),
    ):
        if any(hint in low for hint in hints):
            return intent, 0.55
    if "?" in body:
        return "Question", 0.4
    return "Other", 0.3


def classify_reply(brain: Brain, body: str) -> tuple[str, float]:
    snippet = body.strip()[:1500]
    if not snippet:
        return "Other", 0.0
    try:
        raw = brain.generate(
            CLASSIFY_SYSTEM,
            CLASSIFY_TEMPLATE.format(body=snippet),
            json_mode=True,
            max_tokens=80,
            temperature=0.0,
        )
        parsed = json.loads(raw)
        intent = str(parsed.get("intent", "")).strip().title()
        intent = {"Ooo": "OOO", "Softno": "Soft No", "Hardno": "Hard No"}.get(
            intent.replace(" ", "").title(), intent
        )
        confidence = float(parsed.get("confidence", 0.5))
        if intent in INTENTS:
            return intent, max(0.0, min(1.0, confidence))
        log(f"Model returned unknown intent {intent!r}; using heuristics.", level="WARN")
    except (LLMError, json.JSONDecodeError, ValueError, TypeError) as exc:
        log(f"Classification fell back to heuristics: {exc}", level="WARN")
    return heuristic_intent(snippet)


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------

def next_free_slot(
    cfg: Config, taken: Sequence[datetime]
) -> tuple[datetime, datetime]:
    """First weekday slot at least `lead_time_hours` out that is not taken."""
    now = datetime.now(timezone.utc)
    earliest = now + timedelta(hours=cfg.lead_time_hours)
    local_day = earliest.astimezone(cfg.tz).date()
    taken_utc = {t.astimezone(timezone.utc).replace(microsecond=0) for t in taken}

    for offset in range(0, 60):
        day = local_day + timedelta(days=offset)
        if day.weekday() >= 5:  # Saturday, Sunday
            continue
        for hour in cfg.meeting_hours:
            local_start = datetime.combine(
                day, datetime.min.time().replace(hour=hour), tzinfo=cfg.tz
            )
            start = local_start.astimezone(timezone.utc).replace(microsecond=0)
            if start < earliest or start in taken_utc:
                continue
            return start, start + timedelta(minutes=cfg.meeting_minutes)
    raise RuntimeError("No free meeting slot found in the next 60 days")


def build_ics(
    cfg: Config,
    prof_name: str,
    prof_email: str,
    start: datetime,
    end: datetime,
    uid: str,
) -> bytes:
    try:
        from icalendar import Calendar, Event, vCalAddress, vText
    except ImportError as exc:  # pragma: no cover - import guard
        raise ConfigError("Missing dependency. Run: pip install icalendar") from exc

    cal = Calendar()
    cal.add("prodid", "-//ColdReach//Local Outreach Agent//EN")
    cal.add("version", "2.0")
    cal.add("method", "REQUEST")

    event = Event()
    event.add("uid", uid)
    event.add("summary", f"{cfg.full_name} & Prof. {prof_name.split()[-1]} - intro chat")
    event.add(
        "description",
        f"A {cfg.meeting_minutes} minute conversation about your research. "
        "Reply to this email if another time suits you better.",
    )
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("status", "TENTATIVE")
    event.add("sequence", 0)
    event.add("transp", "OPAQUE")

    organiser = vCalAddress(f"MAILTO:{cfg.email_address}")
    organiser.params["cn"] = vText(cfg.full_name)
    event["organizer"] = organiser

    attendee = vCalAddress(f"MAILTO:{prof_email}")
    attendee.params["cn"] = vText(prof_name)
    attendee.params["role"] = vText("REQ-PARTICIPANT")
    attendee.params["partstat"] = vText("NEEDS-ACTION")
    attendee.params["rsvp"] = vText("TRUE")
    event.add("attendee", attendee, encode=0)

    cal.add_component(event)
    return cal.to_ical()


def _invite_body(cfg: Config, prof_name: str, start: datetime) -> str:
    local = start.astimezone(cfg.tz)
    when = local.strftime("%A %d %B at %I:%M %p").replace(" 0", " ")
    last_name = prof_name.split()[-1] if prof_name.strip() else "there"
    signature = cfg.signature.strip() or f"Best,\n{cfg.full_name}"
    return (
        f"Dear Professor {last_name},\n\n"
        f"Thank you, that is very kind. I have attached a {cfg.meeting_minutes} "
        f"minute hold for {when} ({cfg.timezone}).\n\n"
        "If that time does not suit you, reply with one that does and I will "
        "send a new invitation.\n\n"
        f"{signature}\n"
    )


def send_invite(
    cfg: Config,
    conn: sqlite3.Connection,
    prof: sqlite3.Row,
    in_reply_to: Optional[str],
    reply_subject: str,
) -> bool:
    rows = conn.execute("SELECT start_utc FROM meetings").fetchall()
    taken = [datetime.fromisoformat(r["start_utc"]) for r in rows]
    start, end = next_free_slot(cfg, taken)
    uid = f"coldreach-{prof['id']}-{uuid.uuid4().hex[:12]}@{cfg.email_address.split('@')[-1]}"
    ics = build_ics(cfg, prof["name"], prof["email"], start, end, uid)

    cfg.ics_dir.mkdir(parents=True, exist_ok=True)
    ics_path = cfg.ics_dir / f"{uid.split('@')[0]}.ics"
    ics_path.write_bytes(ics)

    subject = reply_subject if reply_subject.lower().startswith("re:") else f"Re: {reply_subject}"
    msg = _build_message(
        cfg,
        prof["email"],
        prof["name"],
        subject,
        _invite_body(cfg, prof["name"], start),
        in_reply_to=in_reply_to,
    )
    # text/plain + text/calendar alternative renders as an invite in Gmail and
    # Outlook; the .ics attachment covers every other client.
    msg.add_alternative(
        ics.decode("utf-8"),
        subtype="calendar",
        params={"method": "REQUEST", "name": "invite.ics"},
    )
    msg.add_attachment(
        ics, maintype="text", subtype="calendar", filename="invite.ics"
    )

    if cfg.dry_run:
        log(f"[dry-run] would invite {prof['email']} for {start.isoformat()}")
        return False

    try:
        with smtp_session(cfg) as server:
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log(f"Invite failed for {prof['email']}: {exc}", level="ERROR")
        record_event(conn, prof["id"], "invite_failed", str(exc))
        return False

    conn.execute(
        "INSERT INTO meetings (professor_id, uid, start_utc, end_utc, ics_path)"
        " VALUES (?, ?, ?, ?, ?)",
        (prof["id"], uid, start.isoformat(), end.isoformat(), str(ics_path)),
    )
    record_event(conn, prof["id"], "invited", start.isoformat())
    log(f"Invite sent -> {prof['email']} for {start.isoformat()}")
    return True


# --------------------------------------------------------------------------
# Phase 2: poll, classify, act
# --------------------------------------------------------------------------

def poll_replies(cfg: Config, brain: Brain, days: int = 30) -> dict[str, int]:
    """Read the inbox, classify anything new from a contacted professor, act."""
    counts = {"scanned": 0, "new": 0, "scheduled": 0}
    messages = fetch_recent(cfg, days=days)
    counts["scanned"] = len(messages)

    with connect(cfg) as conn:
        contacts = {
            row["email"].lower(): row
            for row in conn.execute(
                "SELECT * FROM professors WHERE sent_at IS NOT NULL"
            ).fetchall()
        }
        if not contacts:
            log("No sent emails yet, nothing to match replies against.")
            return counts

        for msg in messages:
            message_id = (msg.get("Message-ID") or "").strip()
            if not message_id:
                continue
            _, from_addr = email.utils.parseaddr(msg.get("From", ""))
            from_addr = from_addr.lower()
            prof = contacts.get(from_addr)
            if prof is None:
                continue
            already = conn.execute(
                "SELECT 1 FROM inbound WHERE message_id = ?", (message_id,)
            ).fetchone()
            if already:
                continue

            subject = decode_field(msg.get("Subject"))
            body = strip_quotes(extract_body(msg))
            counts["new"] += 1

            if is_auto_reply(msg):
                intent, confidence = "OOO", 1.0
            else:
                intent, confidence = classify_reply(brain, body)

            conn.execute(
                "INSERT INTO inbound (message_id, professor_id, from_addr, subject,"
                " received_at, intent, confidence, snippet)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    prof["id"],
                    from_addr,
                    subject[:200],
                    decode_field(msg.get("Date"))[:100],
                    intent,
                    confidence,
                    body[:500],
                ),
            )
            record_event(conn, prof["id"], "reply", f"{intent} ({confidence:.2f})")
            log(f"Reply from {from_addr}: {intent} ({confidence:.2f})")

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")

            if intent == "OOO":
                # Autoresponder: leave the row alone so the human reply still lands.
                continue

            if intent == "Hard No":
                suppress(conn, from_addr, "hard no")
                set_status(
                    conn, prof["id"], STATUS_HARD_NO, replied_at=now, last_intent=intent
                )
                conn.commit()
                continue

            if intent == "Soft No":
                set_status(
                    conn, prof["id"], STATUS_SOFT_NO, replied_at=now, last_intent=intent
                )
                conn.commit()
                continue

            if intent == "Positive" and prof["status"] != STATUS_SCHEDULED:
                set_status(
                    conn, prof["id"], STATUS_REPLIED, replied_at=now, last_intent=intent
                )
                conn.commit()
                if send_invite(cfg, conn, prof, message_id, subject):
                    set_status(conn, prof["id"], STATUS_SCHEDULED)
                    counts["scheduled"] += 1
                conn.commit()
                continue

            set_status(
                conn, prof["id"], STATUS_REPLIED, replied_at=now, last_intent=intent
            )
            conn.commit()

    return counts


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def status_counts(cfg: Config) -> list[tuple[str, int]]:
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM professors GROUP BY status ORDER BY n DESC"
        ).fetchall()
    return [(r["status"], r["n"]) for r in rows]


def list_by_status(cfg: Config, status: str, limit: int = 50) -> list[sqlite3.Row]:
    with connect(cfg) as conn:
        return conn.execute(
            "SELECT * FROM professors WHERE status = ? ORDER BY id LIMIT ?",
            (status, limit),
        ).fetchall()


def approve(cfg: Config, ids: Optional[Iterable[int]] = None) -> int:
    """Move reviewed drafts into the send queue."""
    with connect(cfg) as conn:
        if ids:
            id_list = list(ids)
            placeholders = ",".join("?" * len(id_list))
            cursor = conn.execute(
                f"UPDATE professors SET status = ?, updated_at = datetime('now')"
                f" WHERE status = ? AND id IN ({placeholders})",
                [STATUS_APPROVED, STATUS_DRAFTED, *id_list],
            )
        else:
            cursor = conn.execute(
                "UPDATE professors SET status = ?, updated_at = datetime('now')"
                " WHERE status = ?",
                (STATUS_APPROVED, STATUS_DRAFTED),
            )
        return cursor.rowcount


def upcoming_meetings(cfg: Config) -> list[sqlite3.Row]:
    with connect(cfg) as conn:
        return conn.execute(
            "SELECT m.*, p.name, p.email FROM meetings m"
            " JOIN professors p ON p.id = m.professor_id"
            " WHERE m.start_utc >= ? ORDER BY m.start_utc",
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchall()
