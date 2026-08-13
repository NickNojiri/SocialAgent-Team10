#!/usr/bin/env python3
"""ColdReach command line.

    python cli.py init                    create professors.db
    python cli.py import contacts.csv     load Name,Email,Research_Topic
    python cli.py add --name ... --email ... --topic ...
    python cli.py draft                   Pending  -> Drafted   (Phase 1)
    python cli.py review                  print drafts for manual reading
    python cli.py approve --all           Drafted  -> Approved
    python cli.py send                    Approved -> Sent
    python cli.py poll                    read inbox, classify, invite (Phase 2)
    python cli.py daily                   draft + poll, the cron entry point
    python cli.py status                  pipeline counts and next meetings
    python cli.py doctor                  check config, Ollama, SMTP, IMAP
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import coldreach as cr


def cmd_init(cfg: cr.Config, args: argparse.Namespace) -> int:
    cr.init_db(cfg)
    return 0


def cmd_import(cfg: cr.Config, args: argparse.Namespace) -> int:
    path = Path(args.csv_path)
    if not path.exists():
        cr.log(f"No such file: {path}", level="ERROR")
        return 1
    cr.init_db(cfg)
    added, skipped = cr.import_csv(cfg, path)
    cr.log(f"Imported {added} contacts, skipped {skipped} (duplicate or invalid).")
    return 0


def cmd_add(cfg: cr.Config, args: argparse.Namespace) -> int:
    cr.init_db(cfg)
    with cr.connect(cfg) as conn:
        ok = cr.add_professor(
            conn, args.name, args.email, args.topic, args.institution or ""
        )
    cr.log("Added." if ok else "That email is already in the database.")
    return 0 if ok else 1


def cmd_draft(cfg: cr.Config, args: argparse.Namespace) -> int:
    cr.init_db(cfg)
    brain = cr.Brain(cfg)
    if not brain.available():
        cr.log("Ollama is not reachable, cannot draft.", level="ERROR")
        return 1
    count = cr.draft_pending(cfg, brain, limit=args.limit)
    cr.log(f"Drafted {count} email(s). Review them with: python cli.py review")
    return 0


def cmd_review(cfg: cr.Config, args: argparse.Namespace) -> int:
    rows = cr.list_by_status(cfg, cr.STATUS_DRAFTED, limit=args.limit)
    if not rows:
        cr.log("No drafts waiting for review.")
        return 0
    for row in rows:
        print("=" * 72)
        print(f"[{row['id']}] {row['name']} <{row['email']}>")
        print(f"Topic  : {row['research_topic']}")
        print(f"Subject: {row['draft_subject']}")
        print("-" * 72)
        print(row["draft_body"])
    print("=" * 72)
    print(f"{len(rows)} draft(s). Approve with: python cli.py approve --all")
    return 0


def cmd_approve(cfg: cr.Config, args: argparse.Namespace) -> int:
    if not args.all and not args.ids:
        cr.log("Pass --all or --ids 1 2 3", level="ERROR")
        return 1
    count = cr.approve(cfg, args.ids if args.ids else None)
    cr.log(f"Approved {count} draft(s). Send with: python cli.py send")
    return 0


def cmd_send(cfg: cr.Config, args: argparse.Namespace) -> int:
    if args.dry_run:
        cfg = cr.replace_dry_run(cfg, True)
    count = cr.send_approved(cfg, limit=args.limit)
    cr.log(f"Sent {count} email(s).")
    return 0


def cmd_poll(cfg: cr.Config, args: argparse.Namespace) -> int:
    cr.init_db(cfg)
    if args.dry_run:
        cfg = cr.replace_dry_run(cfg, True)
    brain = cr.Brain(cfg)
    if not brain.available():
        cr.log("Ollama unreachable; replies will use heuristic classification.", level="WARN")
    counts = cr.poll_replies(cfg, brain, days=args.days)
    cr.log(
        f"Scanned {counts['scanned']} message(s), {counts['new']} new reply(ies), "
        f"{counts['scheduled']} meeting(s) scheduled."
    )
    return 0


def cmd_daily(cfg: cr.Config, args: argparse.Namespace) -> int:
    """Cron entry point. Drafts and listens; never sends cold email unattended."""
    cr.init_db(cfg)
    brain = cr.Brain(cfg)
    if brain.available():
        try:
            cr.draft_pending(cfg, brain, limit=args.limit)
        except Exception as exc:
            cr.log(f"Drafting step failed: {exc}", level="ERROR")
    else:
        cr.log("Skipping drafting, Ollama unreachable.", level="WARN")
    try:
        counts = cr.poll_replies(cfg, brain, days=args.days)
        cr.log(
            f"Daily run complete: {counts['new']} new reply(ies), "
            f"{counts['scheduled']} scheduled."
        )
    except Exception as exc:
        cr.log(f"Polling step failed: {exc}", level="ERROR")
        return 1
    return 0


def cmd_status(cfg: cr.Config, args: argparse.Namespace) -> int:
    counts = cr.status_counts(cfg)
    if not counts:
        cr.log("Database is empty. Import contacts first.")
        return 0
    width = max(len(status) for status, _ in counts)
    print("Pipeline")
    for status, n in counts:
        print(f"  {status.ljust(width)}  {n}")
    meetings = cr.upcoming_meetings(cfg)
    if meetings:
        print("\nUpcoming meetings")
        for m in meetings:
            local = cr.datetime.fromisoformat(m["start_utc"]).astimezone(cfg.tz)
            print(f"  {local:%Y-%m-%d %H:%M}  {m['name']} <{m['email']}>")
    return 0


def cmd_doctor(cfg: cr.Config, args: argparse.Namespace) -> int:
    ok = True
    # Keep every probe bounded so doctor cannot hang on a dead network.
    cfg = cr.dataclasses.replace(cfg, ollama_timeout=min(cfg.ollama_timeout, cfg.net_timeout))

    print(f"Database   : {cfg.db_path} ({'exists' if cfg.db_path.exists() else 'missing'})")
    print(f"Timezone   : {cfg.timezone}")
    print(f"Model      : {cfg.ollama_model} @ {cfg.ollama_host}")

    try:
        cfg.require_email()
        print(f"Identity   : {cfg.full_name} <{cfg.email_address}>")
    except cr.ConfigError as exc:
        print(f"Identity   : FAIL - {exc}")
        ok = False

    brain = cr.Brain(cfg)
    if brain.available():
        try:
            reply = brain.generate("Reply with one word.", "Say: ready", max_tokens=10)
            print(f"Ollama     : OK ({reply[:40]!r})")
        except cr.LLMError as exc:
            print(f"Ollama     : FAIL - {exc}")
            ok = False
    else:
        print("Ollama     : FAIL - not reachable")
        ok = False

    if cfg.email_address and cfg.smtp_password:
        try:
            with cr.smtp_session(cfg):
                print(f"SMTP       : OK ({cfg.smtp_host}:{cfg.smtp_port})")
        except Exception as exc:
            print(f"SMTP       : FAIL - {exc}")
            ok = False
        try:
            with cr.imap_session(cfg):
                print(f"IMAP       : OK ({cfg.imap_host}:{cfg.imap_port})")
        except Exception as exc:
            print(f"IMAP       : FAIL - {exc}")
            ok = False

    try:
        start, end = cr.next_free_slot(cfg, [])
        print(f"Next slot  : {start.astimezone(cfg.tz):%Y-%m-%d %H:%M %Z}")
    except Exception as exc:
        print(f"Next slot  : FAIL - {exc}")
        ok = False

    print("\nAll checks passed." if ok else "\nSome checks failed.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coldreach",
        description="Local-first cold-email agent for academic outreach.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=cr.__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the SQLite database").set_defaults(func=cmd_init)

    p = sub.add_parser("import", help="load contacts from CSV")
    p.add_argument("csv_path")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("add", help="add a single contact")
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--institution")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("draft", help="generate drafts for Pending contacts")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("review", help="print drafts awaiting approval")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("approve", help="mark drafts as ready to send")
    p.add_argument("--all", action="store_true")
    p.add_argument("--ids", type=int, nargs="*")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("send", help="send approved emails")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("poll", help="check inbox, classify replies, send invites")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("daily", help="cron entry point: draft + poll")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_daily)

    sub.add_parser("status", help="show pipeline counts").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="check config and connectivity").set_defaults(
        func=cmd_doctor
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = cr.Config.load()
        return args.func(cfg, args)
    except cr.ConfigError as exc:
        cr.log(str(exc), level="ERROR")
        return 2
    except KeyboardInterrupt:
        cr.log("Interrupted.", level="WARN")
        return 130


if __name__ == "__main__":
    sys.exit(main())
