"""Logins for the team database (docker-compose.teamdb.yml), one per teammate.

    python3 scripts/teamdb_users.py add nojiri "Nicholas Nojiri"   # prints a new password once
    python3 scripts/teamdb_users.py remove nojiri
    python3 scripts/teamdb_users.py list

Writes deploy/teamdb_users.caddy (git-ignored): one "username bcrypt-hash" line per
person, which Caddy reads. Only the hash is stored; the password is shown once, so send
it to that teammate privately (a Discord DM, never a channel or GitHub). After a change:
docker compose -f docker-compose.teamdb.yml restart teamdb-proxy
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
USERS = REPO / "deploy" / "teamdb_users.caddy"
NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def read(path: Path = USERS) -> dict[str, tuple[str, str]]:
    """{username: (hash, full name)}"""
    users = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\S+)\s+(\S+)(?:\s+#\s*(.*))?$", line.strip())
            if m:
                users[m.group(1)] = (m.group(2), m.group(3) or "")
    return users


def write(users: dict[str, tuple[str, str]], path: Path = USERS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{u} {h}" + (f"  # {full}" if full else "") for u, (h, full) in sorted(users.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    try:
        path.chmod(0o640)
    except OSError:
        pass


def add(username: str, full_name: str, path: Path = USERS) -> str:
    if not NAME.match(username):
        raise SystemExit("usernames are lowercase letters, digits, - or _, 2-32 long")
    password = secrets.token_urlsafe(18)
    users = read(path)
    users[username] = (hash_password(password), full_name)
    write(users, path)
    return password


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("username")
    p.add_argument("full_name")
    p = sub.add_parser("remove")
    p.add_argument("username")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    if args.cmd == "add":
        pw = add(args.username, args.full_name)
        print(f"{args.username} ({args.full_name}): password {pw}")
        print("Shown once. Send it to them privately, then restart the proxy.")
    elif args.cmd == "remove":
        users = read()
        print("removed" if users.pop(args.username, None) else "no such user")
        write(users)
    else:
        for u, (_, full) in sorted(read().items()):
            print(f"{u:<16}{full}")


if __name__ == "__main__":
    sys.exit(main())
