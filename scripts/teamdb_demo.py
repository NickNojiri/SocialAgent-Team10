"""Save to and read from the team's central database, from your IDE (CECS 491A, Submission 1).

The central database is SpotBot's own database engine, ChromaDB, running on the team's
Oracle VM behind HTTPS with one login per teammate (docker-compose.teamdb.yml). This
script is the demo: run it from VS Code's terminal.

    python scripts/teamdb_demo.py check                                  # can I reach it, as whom?
    python scripts/teamdb_demo.py save --name "Nicholas Nojiri" --note "Tim Ho Wan, Irvine"
    python scripts/teamdb_demo.py list                                   # every teammate's records

Your login lives in your own .env (never committed):

    TEAMDB_URL=https://db.<the team's name>.duckdns.org
    TEAMDB_USER=<your username>
    TEAMDB_PASSWORD=<the password Nicholas sent you privately>

It writes to one collection, `team_demo`, never to a server's real catalog. Each record
says who saved it and when, so `list` shows every teammate's writes: that's the proof
it's one shared database.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
COLLECTION = "team_demo"
DIM = 64


def load_env(path: Path = REPO / ".env") -> None:
    """Read TEAMDB_* from .env if they aren't already set (no extra dependency)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key.startswith("TEAMDB_") and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"').strip("'")


def settings() -> tuple[str, int, bool, str, str]:
    url, user, pw = (os.environ.get(k, "").strip() for k in ("TEAMDB_URL", "TEAMDB_USER", "TEAMDB_PASSWORD"))
    missing = [k for k, v in (("TEAMDB_URL", url), ("TEAMDB_USER", user), ("TEAMDB_PASSWORD", pw)) if not v]
    if missing:
        raise SystemExit(f"set {', '.join(missing)} in your .env (see the top of this file)")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SystemExit("TEAMDB_URL must look like https://db.example.duckdns.org")
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("refusing to send your password over plain http: use the https address")
    ssl = parts.scheme == "https"
    return parts.hostname, parts.port or (443 if ssl else 80), ssl, user, pw


def embed(texts: list[str]) -> list[list[float]]:
    """A tiny, offline stand-in for an embedding model (hashed words), so the demo never
    downloads a model. SpotBot itself uses mxbai-embed-large through Ollama."""
    out = []
    for text in texts:
        v = [0.0] * DIM
        for word in text.lower().split():
            v[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
        out.append(v)
    return out


def connect():
    import chromadb
    from chromadb.config import Settings

    host, port, ssl, user, pw = settings()
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    client = chromadb.HttpClient(host=host, port=port, ssl=ssl, headers={"Authorization": f"Basic {auth}"},
                                 settings=Settings(anonymized_telemetry=False))
    return client, user


def collection(client):
    return client.get_or_create_collection(COLLECTION, embedding_function=None,
                                           metadata={"purpose": "CECS 491A environment demo"})


def cmd_check(_args) -> None:
    client, user = connect()
    client.heartbeat()
    host = settings()[0]
    print(f"connected to the team database at {host} as {user}")
    print(f"collection '{COLLECTION}': {collection(client).count()} records")


def cmd_save(args) -> None:
    client, user = connect()
    col = collection(client)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rid = f"{user}-{uuid.uuid4().hex[:8]}"
    col.add(ids=[rid], documents=[args.note], embeddings=embed([args.note]),
            metadatas=[{"name": args.name, "login": user, "saved_at": now}])
    got = col.get(ids=[rid], include=["documents", "metadatas"])
    print(f"saved record {rid} to the team database as {user}")
    print(f"read it back: {got['metadatas'][0]['name']} | {got['documents'][0]} | {got['metadatas'][0]['saved_at']}")


def cmd_list(args) -> None:
    client, user = connect()
    col = collection(client)
    where = {"name": args.name} if args.name else None
    got = col.get(where=where, include=["documents", "metadatas"])
    rows = sorted(zip(got["metadatas"], got["documents"], got["ids"]), key=lambda r: r[0].get("saved_at", ""))
    print(f"{len(rows)} record(s) in '{COLLECTION}' (read as {user}):")
    print(f"  {'saved at (UTC)':<26}{'saved by':<20}note")
    for meta, doc, _ in rows[-args.limit:]:
        print(f"  {meta.get('saved_at', ''):<26}{meta.get('name', ''):<20}{doc}")
    people = sorted({m.get("name", "") for m, _, _ in rows})
    print(f"teammates who have saved here: {', '.join(people) or 'nobody yet'}")


def diagnose(exc: Exception) -> str:
    """Why a call failed, in plain words. Chroma reports a refused login as an empty
    error, so ask the server's heartbeat directly with the same login."""
    import urllib.error
    import urllib.request

    msg = str(exc)
    if "401" in msg or "Unauthorized" in msg:
        return "the database refused your login (check TEAMDB_USER and TEAMDB_PASSWORD)"
    try:
        host, port, ssl, user, pw = settings()
        auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
        url = f"{'https' if ssl else 'http'}://{host}:{port}/api/v2/heartbeat"
        urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"}), timeout=10)
    except urllib.error.HTTPError as err:
        if err.code == 401:
            return "the database refused your login (check TEAMDB_USER and TEAMDB_PASSWORD)"
        return f"the server answered HTTP {err.code}"
    except (urllib.error.URLError, OSError) as err:
        return f"can't reach {settings()[0]} ({getattr(err, 'reason', err)}): is the Oracle VM up?"
    except SystemExit as err:
        return str(err)
    return msg or type(exc).__name__


def main(argv=None) -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="connect and say how many records there are")
    p = sub.add_parser("save", help="save one record, then read it back")
    p.add_argument("--name", required=True, help='your full name, e.g. "Nicholas Nojiri"')
    p.add_argument("--note", default="Environment setup demo")
    p = sub.add_parser("list", help="read the records back (everyone's)")
    p.add_argument("--name", help="only this teammate's records")
    p.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    try:
        {"check": cmd_check, "save": cmd_save, "list": cmd_list}[args.cmd](args)
    except SystemExit:
        raise
    except Exception as exc:       # a clear line for the demo instead of a long traceback
        raise SystemExit(f"couldn't use the team database: {diagnose(exc)}") from None


if __name__ == "__main__":
    sys.exit(main())
