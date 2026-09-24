"""Delete what SpotBot keeps for one server: its catalog and its files (Track C #21).

Threat model T6. "Deleted" has to mean the bytes are gone, not just hidden, and
two stores don't do that on their own — found by probing, 2026-09-24:

- Chroma's delete_collection drops the rows, but SQLite keeps the old bytes
  (venue names, voter names) in free pages until the file is rebuilt, so the
  catalog file is VACUUMed afterwards.
- The collection's vector index is a folder named after its segment id. While a
  process still has it open (on Windows, this one), it can't be removed; the
  folders left are written to a marker file and removed on the next start
  (`finish_pending`). Those files hold vectors and ids, not text.

`data/inspirations.jsonl` predates per-server files and has no server id on its
rows. A row is removed from it when its post is in this server's catalog and in
no other server's; a post another server also saved stays, because it is that
server's data too.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("ingestion.forget")

_UUID_DIR = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PENDING_FILE = ".spotbot_forget_pending.json"


def guild_file_key(guild_id: str) -> str:
    """The characters chroma_sink.collection_for_guild keeps — so a server's
    files are named exactly as its catalog is."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(guild_id or ""))


def _chroma_db(chroma_path: Path) -> Path:
    return Path(chroma_path) / "chroma.sqlite3"


def vacuum(db_path: Path, attempts: int = 5) -> bool:
    """Rebuild a SQLite file so deleted rows' bytes are really gone. Another
    process mid-read makes it 'locked' for a moment, so it retries briefly."""
    for attempt in range(attempts):
        try:
            con = sqlite3.connect(str(db_path), timeout=2.0)
            try:
                con.execute("VACUUM")
            finally:
                con.close()
            return True
        except sqlite3.OperationalError as exc:
            log.warning("[forget] VACUUM of %s failed (%s), attempt %d", db_path.name, exc, attempt + 1)
            time.sleep(0.5)
    return False


def _segment_dirs(chroma_path: Path, collection_id: str) -> list[Path]:
    con = sqlite3.connect(str(_chroma_db(chroma_path)))
    try:
        rows = con.execute(
            "SELECT id FROM segments WHERE collection = ?", (collection_id,)
        ).fetchall()
    finally:
        con.close()
    return [Path(chroma_path) / r[0] for r in rows]


def purge_catalog(chroma_path: Path, collection_name: str) -> dict:
    """Drop one server's Chroma collection and rebuild the file without it.

    Returns the ids it held (so the shared JSONL can be scrubbed), the ids any
    other collection still holds, and what, if anything, is left to sweep.
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))
    try:
        col = client.get_collection(collection_name)
    except Exception:
        return {"spots": 0, "ids": set(), "others": set(), "vacuumed": True, "index_dirs_left": 0}
    ids = set(col.get(include=[])["ids"])
    others: set[str] = set()
    for other in client.list_collections():
        name = getattr(other, "name", other)
        if name != collection_name:
            others |= set(client.get_collection(name).get(include=[])["ids"])
    dirs = _segment_dirs(chroma_path, str(col.id))
    client.delete_collection(collection_name)
    vacuumed = vacuum(_chroma_db(chroma_path))
    left = []
    for d in dirs:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            if d.exists():
                left.append(d.name)
    if left or not vacuumed:
        _add_pending(Path(chroma_path), left, vacuum_needed=not vacuumed)
    return {"spots": len(ids), "ids": ids, "others": others,
            "vacuumed": vacuumed, "index_dirs_left": len(left)}


def _add_pending(root: Path, dirs: list[str], *, vacuum_needed: bool) -> None:
    marker = root / PENDING_FILE
    try:
        pending = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pending = {"dirs": [], "vacuum": False}
    pending["dirs"] = sorted(set(pending.get("dirs", [])) | set(dirs))
    pending["vacuum"] = bool(pending.get("vacuum")) or vacuum_needed
    marker.write_text(json.dumps(pending), encoding="utf-8", newline="\n")


def finish_pending(chroma_path: Path) -> dict:
    """Finish a deletion this process couldn't: remove the index folders it
    named and VACUUM if that failed. Run at startup, before any Chroma client
    opens, so nothing holds the folders. Only the folders listed are touched."""
    root = Path(chroma_path)
    marker = root / PENDING_FILE
    if not marker.exists():
        return {"removed": 0, "left": 0}
    try:
        pending = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"removed": 0, "left": 0}
    removed, left = 0, []
    for name in pending.get("dirs", []):
        d = root / name
        if not _UUID_DIR.match(name) or not d.is_dir():
            continue                               # never anything but an index folder
        shutil.rmtree(d, ignore_errors=True)
        if d.exists():
            left.append(name)
        else:
            removed += 1
    vacuum_needed = bool(pending.get("vacuum")) and not vacuum(_chroma_db(root))
    if left or vacuum_needed:
        marker.write_text(json.dumps({"dirs": left, "vacuum": vacuum_needed}),
                          encoding="utf-8", newline="\n")
    else:
        marker.unlink(missing_ok=True)
    return {"removed": removed, "left": len(left)}


def scrub_shared_jsonl(path: Path, remove_ids: set[str]) -> int:
    """Rewrite the legacy shared JSONL without rows for these posts. Returns how many."""
    path = Path(path)
    if not remove_ids or not path.exists():
        return 0
    kept, removed = [], 0
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            content_hash = json.loads(line)["provenance"]["content_hash"]
        except (ValueError, KeyError, TypeError):
            content_hash = None                    # can't tell whose it is: keep it
        if content_hash in remove_ids:
            removed += 1
        else:
            kept.append(line)
    if removed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    return removed


def purge_files(guild_id: str, *, jsonl_dir: Path, raw_root: Path) -> dict:
    """The server's own files: its capture JSONL and its failed-page snapshots."""
    key = guild_file_key(guild_id)
    if not key:
        raise ValueError("refusing to purge files for the shared catalog")
    jsonl = Path(jsonl_dir) / f"{key}.jsonl"
    raw = Path(raw_root) / key
    had_jsonl = jsonl.exists()
    jsonl.unlink(missing_ok=True)
    snapshots = sum(1 for p in raw.rglob("*") if p.is_file()) if raw.exists() else 0
    shutil.rmtree(raw, ignore_errors=True)
    return {"capture_file": had_jsonl, "snapshots": snapshots}
