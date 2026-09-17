"""scripts/ensure_signing_key.py — the step that stops a teammate's first bot run
from hitting a locked catalog (every guild call is refused without the key)."""

import importlib.util
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ensure_signing_key", Path(__file__).parent / "scripts" / "ensure_signing_key.py"
)
esk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(esk)

EXAMPLE = "# Discord — token\nDISCORD_TOKEN=your-discord-bot-token-here\n\nSPOTBOT_SIGNING_KEY=\n"
KEY_LINE = re.compile(r"^SPOTBOT_SIGNING_KEY=([0-9a-f]{64})$", re.MULTILINE)


def _paths(tmp_path, example=EXAMPLE):
    ex = tmp_path / ".env.example"
    ex.write_text(example, encoding="utf-8", newline="")
    return tmp_path / ".env", ex


def test_creates_env_from_example_with_a_key(tmp_path):
    env, ex = _paths(tmp_path)
    assert esk.ensure(env, ex) == "created"
    text = env.read_text(encoding="utf-8")
    assert KEY_LINE.search(text)
    assert "DISCORD_TOKEN=your-discord-bot-token-here" in text   # rest copied as-is
    assert "Discord — token" in text                              # non-ASCII survives


def test_fills_an_empty_key_and_touches_nothing_else(tmp_path):
    env, ex = _paths(tmp_path)
    env.write_text("DISCORD_TOKEN=real-token\r\nSPOTBOT_SIGNING_KEY=\r\nIG_USERNAME=\r\n", encoding="utf-8", newline="")
    assert esk.ensure(env, ex) == "filled"
    raw = env.read_bytes().decode("utf-8")
    assert raw.startswith("DISCORD_TOKEN=real-token\r\nSPOTBOT_SIGNING_KEY=")
    assert raw.endswith("\r\nIG_USERNAME=\r\n")                      # CRLF kept
    assert KEY_LINE.search(raw.replace("\r\n", "\n"))


def test_appends_when_an_old_env_has_no_key_line(tmp_path):
    env, ex = _paths(tmp_path)
    env.write_text("DISCORD_TOKEN=real-token", encoding="utf-8")  # no trailing newline
    assert esk.ensure(env, ex) == "appended"
    lines = env.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "DISCORD_TOKEN=real-token"
    assert KEY_LINE.match(lines[1])


def test_never_replaces_an_existing_key(tmp_path):
    """Rotating the key would revoke every /share link already sent."""
    env, ex = _paths(tmp_path)
    env.write_text("SPOTBOT_SIGNING_KEY=keep-me\n", encoding="utf-8")
    assert esk.ensure(env, ex) == "kept"
    assert env.read_text(encoding="utf-8") == "SPOTBOT_SIGNING_KEY=keep-me\n"


def test_two_runs_give_one_stable_key(tmp_path):
    env, ex = _paths(tmp_path)
    esk.ensure(env, ex)
    first = env.read_text(encoding="utf-8")
    assert esk.ensure(env, ex) == "kept"
    assert env.read_text(encoding="utf-8") == first
    assert len(KEY_LINE.findall(first)) == 1
