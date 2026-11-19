"""The team's central database for the CECS 491A environment demo (docker-compose.teamdb.yml)."""
import importlib.util
import re
import sys
from pathlib import Path

import bcrypt
import pytest
import yaml

ROOT = Path(__file__).parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


demo = _load("teamdb_demo")
users = _load("teamdb_users")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.teamdb.yml").read_text(encoding="utf-8"))
CADDY = (ROOT / "deploy" / "Caddyfile.teamdb").read_text(encoding="utf-8")


def test_chroma_is_reachable_only_through_the_proxy():
    services = COMPOSE["services"]
    assert "ports" not in services["teamdb"]                       # never published on the VM
    assert services["teamdb-proxy"]["ports"] == ["80:80", "443:443"]


def test_the_server_runs_the_same_chroma_version_as_the_app():
    pinned = re.search(r"^chromadb==(\S+)", (ROOT / "requirements.txt").read_text(encoding="utf-8"), re.M).group(1)
    assert COMPOSE["services"]["teamdb"]["image"] == f"chromadb/chroma:{pinned}"


def test_every_api_call_needs_a_login_and_nothing_else_is_served():
    api = CADDY[CADDY.index("handle @api"):CADDY.index("handle {")]
    assert "basic_auth" in api and "import /etc/caddy/teamdb_users.caddy" in api and "reverse_proxy teamdb:8000" in api
    assert 'respond "Not found" 404' in CADDY[CADDY.index("handle {"):]
    assert re.search(r"^\{\$TEAMDB_SITE\} \{$", CADDY, re.M)          # a hostname, so Caddy serves HTTPS


def test_the_logins_file_is_never_committed():
    assert "deploy/teamdb_users.caddy" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_users_get_a_fresh_password_and_only_its_hash_is_stored(tmp_path):
    path = tmp_path / "u.caddy"
    pw = users.add("nojiri", "Nicholas Nojiri", path)
    users.add("reyes", "Kimberly Reyes", path)
    text = path.read_text(encoding="utf-8")
    assert pw not in text and len(pw) >= 20
    stored = users.read(path)
    assert stored["nojiri"][1] == "Nicholas Nojiri"
    assert bcrypt.checkpw(pw.encode(), stored["nojiri"][0].encode())
    assert re.match(r"^nojiri \$2b\$12\$\S+  # Nicholas Nojiri$", text.splitlines()[0])
    with pytest.raises(SystemExit):
        users.add("Bad Name", "x", path)


def test_the_demo_never_sends_a_password_over_plain_http(monkeypatch):
    monkeypatch.setenv("TEAMDB_USER", "nojiri")
    monkeypatch.setenv("TEAMDB_PASSWORD", "x")
    monkeypatch.setenv("TEAMDB_URL", "http://db.example.duckdns.org")
    with pytest.raises(SystemExit, match="plain http"):
        demo.settings()
    monkeypatch.setenv("TEAMDB_URL", "https://db.example.duckdns.org")
    assert demo.settings() == ("db.example.duckdns.org", 443, True, "nojiri", "x")
    monkeypatch.delenv("TEAMDB_PASSWORD")
    with pytest.raises(SystemExit, match="TEAMDB_PASSWORD"):
        demo.settings()


def test_save_then_list_shows_every_teammate(monkeypatch, capsys):
    import chromadb
    client = chromadb.EphemeralClient()
    who = {"login": "nojiri"}
    monkeypatch.setattr(demo, "connect", lambda: (client, who["login"]))
    demo.main(["save", "--name", "Nicholas Nojiri", "--note", "Tim Ho Wan, Irvine"])
    who["login"] = "reyes"
    demo.main(["save", "--name", "Kimberly Reyes", "--note", "Chef Tian, Irvine"])
    out = capsys.readouterr().out
    assert "read it back: Kimberly Reyes | Chef Tian, Irvine" in out
    demo.main(["list"])
    out = capsys.readouterr().out
    assert "teammates who have saved here: Kimberly Reyes, Nicholas Nojiri" in out
    demo.main(["list", "--name", "Nicholas Nojiri"])
    assert "Chef Tian" not in capsys.readouterr().out


def test_a_refused_login_gets_a_plain_message(monkeypatch):
    def boom():
        raise RuntimeError("HTTP 401 Unauthorized")
    monkeypatch.setattr(demo, "connect", boom)
    with pytest.raises(SystemExit, match="refused your login"):
        demo.main(["check"])


def test_an_unreachable_server_says_so(monkeypatch):
    monkeypatch.setenv("TEAMDB_URL", "http://127.0.0.1:9")            # nothing listens on port 9
    monkeypatch.setenv("TEAMDB_USER", "nojiri")
    monkeypatch.setenv("TEAMDB_PASSWORD", "x")
    def boom():
        raise ValueError("")
    monkeypatch.setattr(demo, "connect", boom)
    with pytest.raises(SystemExit, match="can't reach 127.0.0.1"):
        demo.main(["check"])


def test_chroma_cloud_is_used_when_its_key_is_set(monkeypatch):
    import chromadb
    calls = {}
    monkeypatch.setattr(chromadb, "CloudClient", lambda **kw: calls.update(kw) or "cloud-client")
    for k in ("TEAMDB_URL", "TEAMDB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TEAMDB_CLOUD_API_KEY", "ck-test")
    monkeypatch.setenv("TEAMDB_CLOUD_TENANT", "t-1")
    monkeypatch.setenv("TEAMDB_CLOUD_DATABASE", "spotbot-team")
    monkeypatch.setenv("TEAMDB_USER", "reyes")
    assert demo.connect() == ("cloud-client", "reyes")
    assert (calls["api_key"], calls["tenant"], calls["database"]) == ("ck-test", "t-1", "spotbot-team")
    assert demo.where() == "Chroma Cloud (database spotbot-team)"
    monkeypatch.delenv("TEAMDB_USER")
    with pytest.raises(SystemExit, match="TEAMDB_USER"):
        demo.connect()


def test_a_refused_cloud_key_gets_a_plain_message(monkeypatch):
    monkeypatch.setenv("TEAMDB_CLOUD_API_KEY", "bad")
    monkeypatch.setenv("TEAMDB_CLOUD_TENANT", "t")
    monkeypatch.setenv("TEAMDB_CLOUD_DATABASE", "d")
    monkeypatch.setenv("TEAMDB_USER", "reyes")
    def boom():
        raise RuntimeError("403 Forbidden: Permission denied")
    monkeypatch.setattr(demo, "connect", boom)
    with pytest.raises(SystemExit, match="Chroma Cloud refused the key"):
        demo.main(["check"])
