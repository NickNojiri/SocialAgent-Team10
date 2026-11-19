"""Shared test setup: every test gets an empty token revocation list (#30).

The API refuses every tenant request while data/revoked_tokens.json is missing, so tests
get their own empty one instead of touching data/. Tests about the list itself
override SPOTBOT_REVOKED_TOKENS with monkeypatch.
"""
import pytest


@pytest.fixture(autouse=True)
def _empty_revocation_list(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("revoked") / "revoked_tokens.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SPOTBOT_REVOKED_TOKENS", str(path))
    monkeypatch.setenv("SPOTBOT_MINTED_LINKS", str(path.with_name("minted_links.jsonl")))
