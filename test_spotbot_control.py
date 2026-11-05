"""SpotBot Control (scripts/spotbot_control.py): the desktop launcher and dashboard."""
import http.client
import importlib.util
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("spotbot_control", Path(__file__).parent / "scripts" / "spotbot_control.py")
ctl_mod = importlib.util.module_from_spec(_spec)
sys.modules["spotbot_control"] = ctl_mod          # dataclasses look their module up here
_spec.loader.exec_module(ctl_mod)

PY = sys.executable
# A stand-in service: prints its SHARE_BASE_URL, then waits to be stopped.
WAIT = [PY, "-c", "import os,time; print('base=' + os.environ.get('SHARE_BASE_URL', '-'), flush=True); time.sleep(60)"]


def _control(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(ctl_mod, "LOGS", tmp_path / "logs")
    services = [ctl_mod.Service("admin", "Catalog", WAIT), ctl_mod.Service("bot", "Bot", WAIT)]
    return ctl_mod.Control(env={"DISCORD_TOKEN": "x", "SPOTBOT_SIGNING_KEY": "k"}, services=services, **kw)


def _wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_the_tunnel_link_is_read_from_cloudflared_output():
    line = "2026-11-02T10:00:00Z INF |  https://brave-otter-12-mint.trycloudflare.com                    |"
    assert ctl_mod.TUNNEL_URL.search(line).group(0) == "https://brave-otter-12-mint.trycloudflare.com"
    assert ctl_mod.TUNNEL_URL.search("https://evil.example.com/trycloudflare.com") is None


def test_read_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text('DISCORD_TOKEN="abc"\n# comment\nSPOTBOT_SIGNING_KEY = 123 \nnot a line\n', encoding="utf-8")
    assert ctl_mod.read_env_file(f) == {"DISCORD_TOKEN": "abc", "SPOTBOT_SIGNING_KEY": "123"}


def test_start_stop_and_logs(tmp_path, monkeypatch):
    c = _control(tmp_path, monkeypatch)
    assert c.start("admin") == "started" and c.start("admin") == "already running"
    assert _wait(lambda: any(l.startswith("base=") for l in c.by_name["admin"].lines))
    assert c.by_name["admin"].state() == "running"
    assert c.stop("admin") == "stopped" and c.by_name["admin"].state() == "stopped"
    assert "base=" in (tmp_path / "logs" / "admin.log").read_text(encoding="utf-8")


def test_bot_gets_the_public_link_and_restarts_when_it_changes(tmp_path, monkeypatch):
    c = _control(tmp_path, monkeypatch)
    c.start("bot")
    assert _wait(lambda: "base=http://127.0.0.1:8010" in c.by_name["bot"].lines)
    c.saw_tunnel_line("INF |  https://new-link-ab.trycloudflare.com  |")
    assert c.public_url == "https://new-link-ab.trycloudflare.com"
    assert _wait(lambda: "base=https://new-link-ab.trycloudflare.com" in c.by_name["bot"].lines)
    assert c.by_name["bot"].restarts == 1
    c.stop_all()


def test_missing_setup_shows_up_as_problems(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl_mod, "LOGS", tmp_path / "logs")
    c = ctl_mod.Control(env={}, services=[ctl_mod.Service("bot", "Bot", WAIT)])
    probs = " ".join(c.problems())
    assert "DISCORD_TOKEN" in probs and "SPOTBOT_SIGNING_KEY" in probs


DIES = [PY, "-c", "raise SystemExit(3)"]


def test_a_crashed_service_is_restarted_with_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl_mod, "LOGS", tmp_path / "logs")
    c = ctl_mod.Control(env={}, services=[ctl_mod.Service("worker", "Worker", DIES)])
    w = c.by_name["worker"]
    c.start("worker")
    assert _wait(lambda: w.proc.poll() is not None)
    t0 = time.time()
    assert c.watch_once(t0) == [] and w.state() == "crashed"     # noticed: wait 2 s first
    assert c.watch_once(t0 + 1) == []
    assert c.watch_once(t0 + 2.1) == ["worker"] and w.restarts == 1
    assert any("restarting in 2 s" in line for line in w.lines)


def test_a_crash_loop_is_left_stopped_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl_mod, "LOGS", tmp_path / "logs")
    c = ctl_mod.Control(env={"DISCORD_TOKEN": "x", "SPOTBOT_SIGNING_KEY": "k"},
                        services=[ctl_mod.Service("worker", "Worker", DIES)])
    w = c.by_name["worker"]
    c.start("worker")
    now = time.time()
    for _ in range(ctl_mod.CRASH_LIMIT):
        assert _wait(lambda: w.proc is not None and w.proc.poll() is not None)
        c.watch_once(now)                       # count the crash
        now += ctl_mod.BACKOFF_CAP_S
        c.watch_once(now)                       # restart (except after the last one)
    assert not w.wanted and "Kept crashing" in " ".join(c.problems())
    c.start("worker")                           # pressing Start clears it
    assert "Kept crashing" not in " ".join(c.problems())


def test_chaos_monkey_kills_and_self_healing_brings_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl_mod, "LOGS", tmp_path / "logs")
    c = ctl_mod.Control(env={}, services=[ctl_mod.Service("worker", "Worker", WAIT)])
    stop = threading.Event()
    threading.Thread(target=c.loop, args=(stop,), daemon=True).start()
    try:
        c.start("worker")
        assert _wait(lambda: c.by_name["worker"].state() == "running")
        event = c.chaos_kill()
        assert event["service"] == "worker"
        assert _wait(lambda: event["result"] == "recovered", timeout=15)
        assert 0 < event["recovered_s"] < 15
        assert c.by_name["worker"].crashes == []                   # a chaos kill isn't a crash
        assert c.chaos_summary()["recovered"] == 1
    finally:
        stop.set()
        c.stop_all()


def test_chaos_only_runs_when_switched_on_and_due(tmp_path, monkeypatch):
    c = _control(tmp_path, monkeypatch)
    killed = []
    monkeypatch.setattr(c, "chaos_kill", lambda name=None: killed.append(1) or {})
    assert c.chaos_tick() is None and killed == []
    c.set_chaos(True)
    assert c.chaos_tick() is None                                 # not due yet
    assert c.chaos_tick(time.time() + c.chaos["interval_s"] + 1) == {} and killed == [1]
    c.set_chaos(False)
    assert c.chaos_tick(time.time() + 10_000) is None and killed == [1]


def test_tunnel_off_or_missing_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl_mod, "find_cloudflared", lambda: None)
    c = ctl_mod.Control(env={"DISCORD_TOKEN": "x", "SPOTBOT_SIGNING_KEY": "k"})
    assert "winget install Cloudflare.cloudflared" in " ".join(c.problems())
    assert c.start("tunnel").startswith("cloudflared isn't installed")
    off = ctl_mod.Control(tunnel=False, env={"DISCORD_TOKEN": "x", "SPOTBOT_SIGNING_KEY": "k"})
    assert "--no-tunnel" in off.by_name["tunnel"].note


@pytest.fixture
def server(tmp_path, monkeypatch):
    c = _control(tmp_path, monkeypatch)
    monkeypatch.setattr(c, "admin_stats", lambda: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ctl_mod.make_handler(c, "sekret", 0, lambda: None))
    port = srv.server_address[1]
    srv.RequestHandlerClass = ctl_mod.make_handler(c, "sekret", port, lambda: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, c
    srv.shutdown()
    c.stop_all()


def _req(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = conn.getresponse()
    return r.status, r.read()


def test_dashboard_needs_the_session_key_and_a_local_host(server):
    port, c = server
    assert _req(port, "GET", "/")[0] == 403
    assert _req(port, "GET", "/?k=wrong")[0] == 403
    status, body = _req(port, "GET", "/?k=sekret")
    assert status == 200 and b"SpotBot Control" in body
    assert _req(port, "GET", "/?k=sekret", {"Host": f"evil.test:{port}"})[0] == 403      # DNS rebinding
    assert _req(port, "GET", "/api/status")[0] == 404
    assert _req(port, "POST", "/api/admin/start")[0] == 403
    assert c.by_name["admin"].state() == "stopped"


def test_dashboard_buttons_work_with_the_key(server):
    port, c = server
    key = {"X-Control-Key": "sekret"}
    assert _req(port, "POST", "/api/admin/start", key)[0] == 200
    assert _wait(lambda: c.by_name["admin"].state() == "running")
    status, body = _req(port, "GET", "/api/status", key)
    assert status == 200 and b'"state": "running"' in body and b"DISCORD_TOKEN" not in body
    assert _req(port, "POST", "/api/nope/start", key)[0] == 404
    assert _req(port, "POST", "/api/chaos/on")[0] == 403 and not c.chaos["enabled"]
    assert _req(port, "POST", "/api/chaos/on", key)[0] == 200 and c.chaos["enabled"]
    assert _req(port, "POST", "/api/chaos/off", key)[0] == 200 and not c.chaos["enabled"]
    assert _req(port, "POST", "/api/admin/stop", key)[0] == 200
    assert _wait(lambda: c.by_name["admin"].state() == "stopped")
