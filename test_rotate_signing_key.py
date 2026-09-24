"""scripts/rotate_signing_key.py — rotating the tenant signing key (feature #10, T6).

Each test is one promise the runbook makes: the key changes, the old one stops
working, nothing else in .env moves, a backup exists, and no key material is ever
printed.
"""

import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.ingestion.serving import tenant_auth

_spec = importlib.util.spec_from_file_location(
    "rotate_signing_key", Path(__file__).parent / "scripts" / "rotate_signing_key.py"
)
rsk = importlib.util.module_from_spec(_spec)
# @dataclass looks its module up in sys.modules; a module loaded by path isn't there
# until we put it there.
sys.modules[_spec.name] = rsk
_spec.loader.exec_module(rsk)

OLD = "a" * 64
KEY_LINE = re.compile(r"^SPOTBOT_SIGNING_KEY=([0-9a-f]{64})\r?$", re.MULTILINE)


def _env(tmp_path, body=None):
    env = tmp_path / ".env"
    env.write_text(
        body if body is not None else
        f"# Discord — token\r\nDISCORD_TOKEN=real-token\r\nSPOTBOT_SIGNING_KEY={OLD}\r\nIG_USERNAME=\r\n",
        encoding="utf-8", newline="",
    )
    return env


def _key(env):
    return KEY_LINE.search(env.read_bytes().decode("utf-8")).group(1)


def test_rotation_changes_the_key_and_nothing_else(tmp_path):
    env = _env(tmp_path)
    before = env.read_bytes().decode("utf-8")
    rsk.rotate(env, tmp_path / "backups")
    after = env.read_bytes().decode("utf-8")

    new = _key(env)
    assert new != OLD
    assert after == before.replace(OLD, new)          # only the value moved; CRLF and "—" kept
    assert len(KEY_LINE.findall(after)) == 1


def test_the_old_key_stops_verifying_and_the_new_one_works(tmp_path, monkeypatch):
    env = _env(tmp_path)
    monkeypatch.setenv(rsk.ENV_VAR, OLD)
    old_token = tenant_auth.mint_token("g1")
    assert tenant_auth.verify_token(old_token, "g1")

    rsk.rotate(env, tmp_path / "backups")
    monkeypatch.setenv(rsk.ENV_VAR, _key(env))         # what a restarted service reads

    assert not tenant_auth.verify_token(old_token, "g1")   # a leaked token is now dead
    assert tenant_auth.verify_token(tenant_auth.mint_token("g1"), "g1")


def test_a_backup_holds_the_previous_file_exactly(tmp_path):
    env = _env(tmp_path)
    before = env.read_bytes()
    result = rsk.rotate(env, tmp_path / "backups",
                        now=datetime(2026, 9, 24, 1, 2, 3, tzinfo=timezone.utc))
    assert result.backup == tmp_path / "backups" / "env-20260924T010203Z.bak"
    assert result.backup.read_bytes() == before


def test_two_rotations_in_one_second_keep_both_backups(tmp_path):
    env = _env(tmp_path)
    when = datetime(2026, 9, 24, 1, 2, 3, tzinfo=timezone.utc)
    first = rsk.rotate(env, tmp_path / "backups", now=when).backup
    second = rsk.rotate(env, tmp_path / "backups", now=when).backup
    assert first != second and first.exists() and second.exists()
    assert OLD in first.read_text(encoding="utf-8")    # the very first key is still recoverable


@pytest.mark.parametrize("body", [None, "DISCORD_TOKEN=x\n", "SPOTBOT_SIGNING_KEY=\n"])
def test_refuses_when_there_is_nothing_to_rotate(tmp_path, body):
    env = tmp_path / ".env"
    if body is not None:
        env.write_text(body, encoding="utf-8")
    with pytest.raises(rsk.RotationRefused):
        rsk.rotate(env, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()         # refused means nothing was written
    if body is not None:
        assert env.read_text(encoding="utf-8") == body


def test_a_failed_write_leaves_env_untouched_and_no_temp_file(tmp_path, monkeypatch):
    env = _env(tmp_path)
    before = env.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(rsk.os, "replace", boom)
    with pytest.raises(OSError):
        rsk.rotate(env, tmp_path / "backups")
    assert env.read_bytes() == before
    assert not (tmp_path / ".env.rotating").exists()   # it held the new key
    assert list((tmp_path / "backups").iterdir())      # and the backup is still there


def test_without_confirm_nothing_changes(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    before = env.read_bytes()
    monkeypatch.setattr(rsk, "REPO", tmp_path)
    assert rsk.main([]) == 2
    assert env.read_bytes() == before
    assert "--confirm" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()


def test_no_key_material_is_ever_printed(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    monkeypatch.setattr(rsk, "REPO", tmp_path)
    assert rsk.main(["--confirm"]) == 0
    new = _key(env)

    printed = "".join(capsys.readouterr())
    for key in (OLD, new):
        # not the key, and not even a recognisable piece of it
        assert not any(key[i:i + 8] in printed for i in range(0, 57))
    assert "restart" in printed and "share" in printed
    assert "data" in printed and "key-backups" in printed


def test_backups_land_somewhere_git_ignores():
    """data/ and every .env.* copy are gitignored; a backup next to .env would not be."""
    ignore = (Path(__file__).parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in ignore
    assert ".env.*" in ignore and "!.env.example" in ignore
