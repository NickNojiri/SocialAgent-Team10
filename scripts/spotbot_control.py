"""SpotBot Control: the desktop launcher and admin dashboard.

Double-click the "SpotBot Control" shortcut (scripts/install_shortcut.ps1 makes it) and
this starts the whole stack on this computer, then opens a dashboard window:

  admin      the catalog + capture service              127.0.0.1:8010
  recommend  /plan and /events                          127.0.0.1:8003
  gate       the only door the tunnel may use           127.0.0.1:8080  (serving/gate.py)
  tunnel     Cloudflare quick tunnel -> gate, gives an https:// link phones can open anywhere
  bot        the Discord bot; its /app links use the tunnel link

Everything runs and is stored here. The tunnel only relays traffic for the gate, which
lets through the phone app and token-checked calls, nothing else.

Self-healing: a service that dies on its own is restarted, waiting 2, 4, 8 … 60 s between
tries; after 5 crashes in 5 minutes it is left stopped and listed under "Needs attention".
Chaos monkey (off by default, a switch in the window): every few minutes it kills one
running service at random and records how long SpotBot took to be healthy again.

The dashboard listens on 127.0.0.1 only and needs a per-session key, so web pages in
your browser can't press its buttons. Closing the window leaves SpotBot running; the
shortcut reopens it. Quit (in the window) stops everything.

    python scripts/spotbot_control.py [--no-tunnel] [--no-window] [--port 8090]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "data" / "logs"
SESSION = ROOT / "data" / "control_session.json"
ADMIN = "http://127.0.0.1:8010"
HEALTH = {"admin": ADMIN + "/health", "recommend": "http://127.0.0.1:8003/health",
          "gate": "http://127.0.0.1:8080/app"}
CRASH_LIMIT, CRASH_WINDOW_S, BACKOFF_CAP_S = 5, 300, 60
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW


def python_exe() -> str:
    """The venv's python.exe (not pythonw: children need real stdout pipes)."""
    for p in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    exe = Path(sys.executable)
    return str(exe.with_name("python.exe")) if exe.name.lower() == "pythonw.exe" else sys.executable


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for p in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe")):
        if os.path.exists(p):
            return p
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines from .env (same rule as run_local.ps1)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip()
    return out


@dataclass
class Service:
    name: str
    label: str
    cmd: list[str]
    proc: subprocess.Popen | None = None
    started: float = 0.0
    restarts: int = 0
    lines: deque = field(default_factory=lambda: deque(maxlen=300))
    wanted: bool = False
    note: str = ""
    crashes: list = field(default_factory=list)     # when it died on its own
    next_try: float = 0.0
    chaos_hit: bool = False                         # killed on purpose: restart, don't count it

    def state(self) -> str:
        if self.proc is None:
            return "stopped"
        return "running" if self.proc.poll() is None else ("crashed" if self.wanted else "stopped")


class Control:
    """Starts, stops and watches the services; knows the tunnel's public link."""

    def __init__(self, tunnel: bool = True, env: dict | None = None, services: list[Service] | None = None):
        self.lock = threading.RLock()
        self.public_url = ""
        self.stats: dict | None = None
        self.stats_at = 0.0
        self.env = env if env is not None else self._base_env()
        py, cf = python_exe(), find_cloudflared()
        self.services = services or [
            Service("admin", "Catalog + capture", [py, "-m", "uvicorn", "src.ingestion.serving.admin:app", "--host", "127.0.0.1", "--port", "8010"]),
            Service("recommend", "Plans + search", [py, "-m", "uvicorn", "src.ingestion.serving.app:app", "--host", "127.0.0.1", "--port", "8003"]),
            Service("gate", "Gatekeeper", [py, "-m", "uvicorn", "src.ingestion.serving.gate:app", "--host", "127.0.0.1", "--port", "8080"]),
            Service("tunnel", "Public link", [cf, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8080"] if cf else []),
            Service("bot", "Discord bot", [py, "app/bot.py"]),
        ]
        self.by_name = {s.name: s for s in self.services}
        self.chaos = {"enabled": False, "interval_s": 180, "next_at": 0.0, "history": deque(maxlen=50)}
        self.rng = random.Random()
        if "tunnel" in self.by_name:
            t = self.by_name["tunnel"]
            if not tunnel:
                t.cmd, t.note = [], "Turned off (--no-tunnel). /app links work on this computer only."
            elif not t.cmd:
                t.note = "cloudflared isn't installed. Run: winget install Cloudflare.cloudflared"

    @staticmethod
    def _base_env() -> dict:
        env = dict(os.environ)
        env.update(read_env_file(ROOT / ".env"))
        env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "OLLAMA_URL": "http://localhost:11434",
                    "INGEST_URL": ADMIN, "ADMIN_URL": ADMIN, "RECOMMEND_URL": "http://127.0.0.1:8003",
                    "GATE_UPSTREAM": ADMIN})
        # Durable capture jobs (ADR-0005): a crash, or the chaos monkey, mid-capture
        # requeues the job on restart instead of losing it.
        env.setdefault("JOB_STORE", "sqlite")
        return env

    # ── processes ─────────────────────────────────────────────────────────

    def start(self, name: str) -> str:
        with self.lock:
            s = self.by_name[name]
            if s.note.startswith("Kept crashing"):
                s.note, s.crashes = "", []
            if not s.cmd:
                return s.note or "not available"
            if s.state() == "running":
                return "already running"
            env = dict(self.env)
            if name == "bot":
                env["SHARE_BASE_URL"] = self.public_url or ADMIN
            LOGS.mkdir(parents=True, exist_ok=True)
            s.proc = subprocess.Popen(s.cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                      creationflags=NO_WINDOW)
            s.started, s.wanted = time.time(), True
            s.lines.append(f"── started {time.strftime('%H:%M:%S')} ──")
            threading.Thread(target=self._pump, args=(s, s.proc), daemon=True).start()
            return "started"

    def stop(self, name: str) -> str:
        with self.lock:
            s = self.by_name[name]
            s.wanted = False
            if s.proc is None or s.proc.poll() is not None:
                return "not running"
            s.proc.terminate()
        try:
            s.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            s.proc.kill()
        if name == "tunnel":
            self.public_url = ""
        s.lines.append(f"── stopped {time.strftime('%H:%M:%S')} ──")
        return "stopped"

    def restart(self, name: str) -> str:
        self.stop(name)
        self.by_name[name].restarts += 1
        return self.start(name)

    def start_all(self) -> None:
        for name in ("admin", "recommend", "gate", "tunnel"):
            if name in self.by_name:
                self.start(name)
        # The bot's /app links must carry the public link, so give the tunnel a moment.
        t = self.by_name.get("tunnel")
        deadline = time.time() + 30
        while t and t.cmd and not self.public_url and t.state() == "running" and time.time() < deadline:
            time.sleep(0.5)
        if "bot" in self.by_name:
            self.start("bot")

    def stop_all(self) -> None:
        for s in reversed(self.services):
            self.stop(s.name)

    def _pump(self, s: Service, proc: subprocess.Popen) -> None:
        log = (LOGS / f"{s.name}.log").open("a", encoding="utf-8", newline="\n")
        try:
            for line in proc.stdout:
                line = line.rstrip()
                s.lines.append(line)
                log.write(line + "\n")
                log.flush()
                if s.name == "tunnel":
                    self.saw_tunnel_line(line)
        finally:
            log.close()

    def saw_tunnel_line(self, line: str) -> None:
        m = TUNNEL_URL.search(line)
        if not m or m.group(0) == self.public_url:
            return
        self.public_url = m.group(0)
        bot = self.by_name.get("bot")
        # A bot started with an old link would hand out dead /app links: restart it.
        if bot and bot.state() == "running":
            threading.Thread(target=self.restart, args=("bot",), daemon=True).start()

    # ── self-healing ──────────────────────────────────────────────────────

    def watch_once(self, now: float | None = None) -> list[str]:
        """Restart services that died on their own. Returns the names restarted."""
        now = time.time() if now is None else now
        restarted = []
        for s in self.services:
            if not (s.wanted and s.proc is not None and s.proc.poll() is not None):
                continue
            if s.chaos_hit:                             # the monkey did it: come straight back
                s.chaos_hit, s.next_try = False, now + 1
                s.lines.append("── restarting after the chaos monkey ──")
                continue
            if not s.next_try:                          # just noticed: count it, wait
                s.crashes = [t for t in s.crashes if now - t < CRASH_WINDOW_S] + [now]
                if len(s.crashes) >= CRASH_LIMIT:
                    s.wanted = False
                    s.note = f"Kept crashing ({CRASH_LIMIT} times in {CRASH_WINDOW_S // 60} min). Check its log, fix it, then press Start."
                    s.lines.append("── gave up restarting ──")
                    continue
                s.next_try = now + min(BACKOFF_CAP_S, 2 ** len(s.crashes))
                s.lines.append(f"── exited (code {s.proc.returncode}); restarting in {int(s.next_try - now)} s ──")
            elif now >= s.next_try:
                s.next_try = 0.0
                s.restarts += 1
                s.proc = None
                self.start(s.name)
                restarted.append(s.name)
        return restarted

    def healthy(self, s: Service) -> bool:
        if s.state() != "running":
            return False
        if s.name == "tunnel":
            return bool(self.public_url)
        url = HEALTH.get(s.name)
        if not url:
            return True
        try:
            with urlopen(url, timeout=2) as r:
                return r.status < 500
        except Exception:
            return False

    # ── chaos monkey ──────────────────────────────────────────────────────

    def set_chaos(self, enabled: bool) -> None:
        self.chaos["enabled"] = enabled
        self.chaos["next_at"] = time.time() + self.chaos["interval_s"] if enabled else 0.0

    def chaos_kill(self, name: str | None = None) -> dict | None:
        """Kill one running service the hard way (as a crash would), then time its recovery."""
        running = [s for s in self.services if s.state() == "running" and (name is None or s.name == name)]
        if not running:
            return None
        s = self.rng.choice(running)
        event = {"service": s.name, "label": s.label, "at": time.strftime("%H:%M:%S"), "killed": time.time(),
                 "recovered_s": None, "result": "recovering"}
        s.lines.append("── 🐒 chaos monkey killed this ──")
        s.chaos_hit = True
        s.proc.kill()
        self.chaos["history"].appendleft(event)
        threading.Thread(target=self._time_recovery, args=(s, event), daemon=True).start()
        return event

    def _time_recovery(self, s: Service, event: dict, timeout_s: float = 180) -> None:
        while time.time() - event["killed"] < timeout_s:
            if s.started > event["killed"] and self.healthy(s):
                event["recovered_s"] = round(time.time() - event["killed"], 1)
                event["result"] = "recovered"
                return
            time.sleep(0.5)
        event["result"] = "did not recover"

    def chaos_tick(self, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        if not self.chaos["enabled"] or now < self.chaos["next_at"]:
            return None
        self.chaos["next_at"] = now + self.chaos["interval_s"]
        return self.chaos_kill()

    def chaos_summary(self) -> dict:
        h = list(self.chaos["history"])
        times = sorted(e["recovered_s"] for e in h if e["recovered_s"] is not None)
        return {"kills": len(h), "recovered": len(times), "failed": sum(1 for e in h if e["result"] == "did not recover"),
                "median_s": times[len(times) // 2] if times else None, "worst_s": times[-1] if times else None}

    def loop(self, stop: threading.Event) -> None:
        """The background heartbeat: self-healing every second, chaos when it's due."""
        while not stop.wait(1.0):
            try:
                self.watch_once()
                self.chaos_tick()
            except Exception as exc:                    # the heartbeat must never die
                print(f"[control] heartbeat error: {exc}")

    # ── what the dashboard shows ──────────────────────────────────────────

    def admin_stats(self) -> dict | None:
        """The admin service's /api/stats, at most 10 s old. Fetched in the background
        (it probes Ollama and reads every catalog), so the window never waits on it."""
        if time.time() - self.stats_at > 10:
            self.stats_at = time.time()
            threading.Thread(target=self._fetch_stats, daemon=True).start()
        return self.stats

    def _fetch_stats(self) -> None:
        try:
            with urlopen(ADMIN + "/api/stats", timeout=15) as r:
                self.stats = json.loads(r.read().decode("utf-8"))
        except Exception:
            self.stats = None

    def problems(self) -> list[str]:
        env, out = self.env, []
        token = env.get("DISCORD_TOKEN", "")
        if not token or "your-discord-bot-token" in token:
            out.append("DISCORD_TOKEN isn't set in .env, so the bot can't start.")
        if not env.get("SPOTBOT_SIGNING_KEY"):
            out.append("SPOTBOT_SIGNING_KEY is missing. Run: python scripts/ensure_signing_key.py")
        t = self.by_name.get("tunnel")
        if t and t.note:
            out.append(t.note)
        st = self.stats or {}
        if st and not (st.get("services") or {}).get("ollama"):
            out.append("Ollama isn't running: new spots can't be saved. Open the Ollama app.")
        for s in self.services:
            if s.note.startswith("Kept crashing"):
                out.append(f"{s.label}: {s.note}")
        return out

    def status(self) -> dict:
        now = time.time()
        return {
            "public_url": self.public_url,
            "app_url": (self.public_url or ADMIN) + "/app",
            "services": [{"name": s.name, "label": s.label, "state": s.state(), "available": bool(s.cmd),
                          "uptime_s": int(now - s.started) if s.state() == "running" else 0,
                          "restarts": s.restarts, "log": list(s.lines)[-60:], "note": s.note}
                         for s in self.services],
            "stats": self.admin_stats(),
            "problems": self.problems(),
            "chaos": {"enabled": self.chaos["enabled"], "interval_s": self.chaos["interval_s"],
                      "next_in_s": max(0, int(self.chaos["next_at"] - now)) if self.chaos["enabled"] else None,
                      "history": [{k: v for k, v in e.items() if k != "killed"} for e in list(self.chaos["history"])[:15]],
                      "summary": self.chaos_summary()},
        }


# ── the dashboard server ──────────────────────────────────────────────────


def make_handler(ctl: Control, key: str, port: int, on_quit):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _ok(self, need_header: bool) -> bool:
            # Host check stops DNS-rebinding pages; the key stops other local pages.
            if self.headers.get("Host", "") not in hosts:
                return False
            given = self.headers.get("X-Control-Key", "") if need_header else \
                parse_qs(urlsplit(self.path).query).get("k", [""])[0]
            return secrets.compare_digest(given, key)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                if not self._ok(False):
                    return self._send(403, b"Open SpotBot Control from its shortcut.", "text/plain")
                return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/status" and self._ok(True):
                return self._send(200, json.dumps(ctl.status()).encode("utf-8"))
            self._send(404, b"{}")

        def do_POST(self):
            if not self._ok(True):
                return self._send(403, b"{}")
            m = re.fullmatch(r"/api/(\w+)/(start|stop|restart)", urlsplit(self.path).path)
            cm = re.fullmatch(r"/api/chaos/(on|off|now)", urlsplit(self.path).path)
            if cm:
                act = cm.group(1)
                if act == "now":
                    threading.Thread(target=ctl.chaos_kill, daemon=True).start()
                else:
                    ctl.set_chaos(act == "on")
                return self._send(200, b'{"ok":true}')
            if urlsplit(self.path).path == "/api/quit":
                self._send(200, b'{"ok":true}')
                return threading.Thread(target=on_quit, daemon=True).start()
            if not m:
                return self._send(404, b"{}")
            name, action = m.groups()
            if name == "all":
                fn = ctl.start_all if action == "start" else ctl.stop_all if action == "stop" else \
                    (lambda: (ctl.stop_all(), ctl.start_all()))
                threading.Thread(target=fn, daemon=True).start()
                return self._send(200, b'{"ok":true}')
            if name not in ctl.by_name:
                return self._send(404, b"{}")
            threading.Thread(target=getattr(ctl, action), args=(name,), daemon=True).start()
            self._send(200, b'{"ok":true}')

    return Handler


def open_window(url: str) -> None:
    """An app-style window (no tabs or address bar) in Edge or Chrome, else the browser."""
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                shutil.which("chromium") or "", shutil.which("google-chrome") or ""):
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1280,860"], creationflags=NO_WINDOW)
            return
    webbrowser.open(url)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--no-tunnel", action="store_true")
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()

    # Already running? Just reopen its window.
    if SESSION.exists():
        try:
            old = json.loads(SESSION.read_text(encoding="utf-8"))
            urlopen(f"http://127.0.0.1:{old['port']}/?k={old['key']}", timeout=2)
            if not args.no_window:
                open_window(f"http://127.0.0.1:{old['port']}/?k={old['key']}")
            return
        except Exception:
            pass

    if (ROOT / ".env").exists():
        subprocess.run([python_exe(), "scripts/ensure_signing_key.py"], cwd=ROOT, creationflags=NO_WINDOW)
    ctl = Control(tunnel=not args.no_tunnel)
    key = secrets.token_urlsafe(24)
    done = threading.Event()

    def quit_all():
        ctl.set_chaos(False)
        ctl.stop_all()
        done.set()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(ctl, key, args.port, quit_all))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps({"port": args.port, "key": key}), encoding="utf-8")
    url = f"http://127.0.0.1:{args.port}/?k={key}"
    print(f"SpotBot Control: {url}")
    threading.Thread(target=ctl.start_all, daemon=True).start()
    beat = threading.Event()
    threading.Thread(target=ctl.loop, args=(beat,), daemon=True).start()
    if not args.no_window:
        open_window(url)
    try:
        done.wait()
    except KeyboardInterrupt:
        ctl.stop_all()
    finally:
        beat.set()
        server.shutdown()
        try:
            SESSION.unlink()
        except OSError:
            pass


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SpotBot Control</title>
<style>
  :root { color-scheme: dark; --bg:#0b0a14; --card:#16142a; --card2:#1f1c3a; --line:rgba(255,255,255,.09); --ink:#f1f0fb;
          --ink2:#b4b1cf; --ink3:#807ca1; --green:#34d399; --amber:#fbbf24; --red:#f87171; --pink:#ff3d8b;
          --grad: linear-gradient(110deg,#7c5cff,#ff3d8b 55%,#ff8a4c); }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 "Segoe UI", system-ui, sans-serif; }
  header { display:flex; align-items:center; gap:14px; padding:16px 22px; border-bottom:1px solid var(--line);
           background:linear-gradient(180deg,#141229,#0b0a14); position:sticky; top:0; z-index:2; }
  header h1 { margin:0; font-size:20px; } header .sp { flex:1; }
  .pill { padding:4px 10px; border-radius:999px; font-size:12px; font-weight:700; background:var(--card2); }
  .pill.ok { background:rgba(52,211,153,.15); color:var(--green); } .pill.bad { background:rgba(248,113,113,.15); color:var(--red); }
  .pill.warn { background:rgba(251,191,36,.15); color:var(--amber); }
  button { font:inherit; color:inherit; border:0; border-radius:10px; padding:8px 14px; background:var(--card2); cursor:pointer; font-weight:600; }
  button:hover { filter:brightness(1.2); } button.primary { background:var(--grad); color:#fff; } button.danger { background:#3b1420; color:var(--red); }
  button:focus-visible { outline:2px solid var(--pink); outline-offset:2px; }
  main { padding:18px 22px 40px; display:grid; gap:16px; grid-template-columns: repeat(12, 1fr); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px; }
  .card h2 { margin:0 0 12px; font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink3); }
  .s12 { grid-column: span 12; } .s8 { grid-column: span 8; } .s4 { grid-column: span 4; } .s6 { grid-column: span 6; }
  @media (max-width: 960px) { .s8, .s4, .s6 { grid-column: span 12; } }
  .link { font:600 18px/1.3 ui-monospace, Consolas, monospace; word-break:break-all; }
  .stats { display:grid; grid-template-columns: repeat(auto-fit, minmax(130px,1fr)); gap:10px; }
  .stat { background:var(--card2); border-radius:12px; padding:12px; } .stat b { display:block; font-size:26px; }
  .stat span { color:var(--ink3); font-size:12px; }
  table { width:100%; border-collapse:collapse; } th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); }
  th { color:var(--ink3); font-weight:600; font-size:12px; }
  .svc { display:flex; align-items:center; gap:10px; padding:10px 0; border-bottom:1px solid var(--line); }
  .svc:last-child { border:0; } .svc .nm { flex:1; } .svc small { color:var(--ink3); display:block; }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--ink3); flex:none; }
  .dot.running { background:var(--green); box-shadow:0 0 10px var(--green); } .dot.crashed { background:var(--red); }
  pre { margin:0; background:#07060e; border-radius:12px; padding:12px; height:300px; overflow:auto; font:12px/1.5 ui-monospace, Consolas, monospace; color:#cfcbe8; white-space:pre-wrap; }
  .problems li { color:var(--amber); margin:4px 0; } .muted { color:var(--ink3); }
  .tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; } .tabs button[aria-pressed=true] { background:var(--ink); color:var(--bg); }
  a { color:#ff8fbd; }
</style></head>
<body>
<header><h1>📍 SpotBot Control</h1><span id="overall" class="pill">…</span><span class="sp"></span>
  <button class="primary" data-act="all/start">▶ Start all</button><button data-act="all/restart">↻ Restart all</button>
  <button data-act="all/stop">■ Stop all</button><button class="danger" id="quit">Quit</button></header>
<main>
  <section class="card s8"><h2>Phone app link</h2>
    <div class="link" id="url">Starting…</div>
    <p class="muted" id="urlnote"></p>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button id="copy">Copy link</button>
      <button data-open="http://127.0.0.1:8010/dash">Operations dashboard</button>
      <button data-open="http://127.0.0.1:8010/">Catalog admin</button>
      <button data-open="http://127.0.0.1:8010/app">Phone app (demo)</button></div>
  </section>
  <section class="card s4"><h2>Needs attention</h2><ul class="problems" id="problems"></ul></section>
  <section class="card s12"><h2>At a glance</h2><div class="stats" id="stats"></div></section>
  <section class="card s4"><h2>Services</h2><div id="svcs"></div></section>
  <section class="card s8"><h2>Logs</h2><div class="tabs" id="logtabs"></div><pre id="log"></pre></section>
  <section class="card s12"><h2>🐒 Chaos monkey</h2>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
      <button id="chaosToggle">Turn on</button><button id="chaosNow">Kill one now</button>
      <span class="muted" id="chaosNote"></span></div>
    <div class="stats" id="chaosStats" style="margin-bottom:10px"></div>
    <table><thead><tr><th>Time</th><th>Killed</th><th>Result</th><th>Back in</th></tr></thead><tbody id="chaosRows"></tbody></table>
  </section>
  <section class="card s6"><h2>Servers</h2><table><thead><tr><th>Server</th><th>Spots</th><th>Votes</th><th>Nights out</th><th>Last capture</th></tr></thead><tbody id="servers"></tbody></table></section>
  <section class="card s6"><h2>Captures</h2><table><tbody id="caps"></tbody></table></section>
</main>
<script>
"use strict";
const KEY = new URLSearchParams(location.search).get("k") || "";
history.replaceState(null, "", "/");
let logFor = "admin", last = null;
const $ = id => document.getElementById(id);
function el(tag, attrs, ...kids) { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs || {})) { if (k === "on") e.addEventListener("click", v); else e.setAttribute(k, v); }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(String(k))); return e; }
async function call(path) { await fetch("/api/" + path, { method: "POST", headers: { "X-Control-Key": KEY } }); setTimeout(refresh, 600); }
function dur(s) { if (!s) return ""; const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60; return h ? `${h}h ${m}m` : `${m}m ${s % 60}s`; }
function stat(label, value) { return el("div", { class: "stat" }, el("b", {}, value == null ? "—" : value), el("span", {}, label)); }
function render(d) {
  last = d;
  const up = d.services.filter(s => s.available), running = up.filter(s => s.state === "running").length;
  const o = $("overall"); o.textContent = running === up.length ? "All running" : running ? `${running}/${up.length} running` : "Stopped";
  o.className = "pill " + (running === up.length ? "ok" : running ? "warn" : "bad");
  $("url").textContent = d.public_url ? d.app_url : (d.services.find(s => s.name === "tunnel")?.state === "running" ? "Waiting for the tunnel…" : d.app_url);
  $("urlnote").textContent = d.public_url ? "Works from any network. Type /app in Discord for your personal link; it uses this address." : "Only this computer can open this link. Start the tunnel to reach phones on any network.";
  $("problems").replaceChildren(...(d.problems.length ? d.problems.map(p => el("li", {}, p)) : [el("li", { style: "color:var(--green)" }, "Nothing. All good.")]));
  const st = d.stats || {}, t = st.totals || {}, q = st.queue || {}, ttc = (st.time_to_card || {}).all || {}, ch = st.capture_health || {};
  const states = ch.states || {}, ok = states.done || 0, tot = ch.captures || 0;
  $("stats").replaceChildren(stat("Servers", t.servers), stat("Spots saved", t.spots), stat("Votes", t.votes), stat("Nights out", t.nights),
    stat("Captures waiting", st.queue ? q.waiting : null), stat("Capturing now", st.queue ? q.running : null),
    stat("Capture success", tot ? Math.round(100 * ok / tot) + "%" : null), stat("Paste → card (median)", ttc.median_s != null ? ttc.median_s + " s" : null),
    stat("Ollama", st.services ? (st.services.ollama ? "on" : "off") : null));
  $("svcs").replaceChildren(...d.services.map(s => el("div", { class: "svc" }, el("span", { class: "dot " + s.state }),
    el("div", { class: "nm" }, s.label, el("small", {}, s.available ? `${s.state}${s.uptime_s ? " · " + dur(s.uptime_s) : ""}${s.restarts ? " · " + s.restarts + " restarts" : ""}` : (s.note || "not available"))),
    s.available ? el("button", { on: () => call(s.name + (s.state === "running" ? "/stop" : "/start")) }, s.state === "running" ? "Stop" : "Start") : null,
    s.available ? el("button", { on: () => call(s.name + "/restart") }, "↻") : null)));
  $("logtabs").replaceChildren(...d.services.map(s => el("button", { "aria-pressed": String(s.name === logFor), on: () => { logFor = s.name; render(last); } }, s.label)));
  const pre = $("log"), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = (d.services.find(s => s.name === logFor)?.log || []).join("\n") || "No output yet.";
  if (atEnd) pre.scrollTop = pre.scrollHeight;
  $("servers").replaceChildren(...((st.tenants || []).map(g => el("tr", {}, el("td", {}, g.guild), el("td", {}, g.spots), el("td", {}, g.votes), el("td", {}, g.nights), el("td", {}, g.last || "—")))));
  if (!(st.tenants || []).length) $("servers").append(el("tr", {}, el("td", { colspan: "5", class: "muted" }, d.stats ? "No servers yet." : "Start the catalog service to see servers.")));
  const rows = [["Captures logged", tot || "—"], ["Succeeded", ok], ["Failed", states.failed || 0],
    ["Median capture time", ch.duration_s ? ch.duration_s.median + " s" : "—"], ["Slowest (p95)", ch.duration_s ? ch.duration_s.p95 + " s" : "—"],
    ["Failed in the last hour", st.queue ? q.failed_recent : "—"]];
  $("caps").replaceChildren(...rows.map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", {}, v))));
  const c = d.chaos, cs = c.summary;
  $("chaosToggle").textContent = c.enabled ? "Turn off" : "Turn on";
  $("chaosToggle").className = c.enabled ? "danger" : "";
  $("chaosNote").textContent = c.enabled ? `On: kills a random running service every ${Math.round(c.interval_s / 60)} min. Next in ${dur(c.next_in_s) || "a moment"}.`
    : "Off. When on, it kills a random running service every few minutes to prove SpotBot heals itself.";
  $("chaosStats").replaceChildren(stat("Kills", cs.kills), stat("Recovered", cs.recovered), stat("Didn't recover", cs.failed),
    stat("Median recovery", cs.median_s != null ? cs.median_s + " s" : null), stat("Slowest", cs.worst_s != null ? cs.worst_s + " s" : null));
  $("chaosRows").replaceChildren(...(c.history.length ? c.history.map(e => el("tr", {}, el("td", {}, e.at), el("td", {}, e.label),
    el("td", { style: "color:" + (e.result === "recovered" ? "var(--green)" : e.result === "recovering" ? "var(--amber)" : "var(--red)") }, e.result),
    el("td", {}, e.recovered_s != null ? e.recovered_s + " s" : "—"))) : [el("tr", {}, el("td", { colspan: "4", class: "muted" }, "Nothing killed yet."))]));
}
async function refresh() {
  try { const r = await fetch("/api/status", { headers: { "X-Control-Key": KEY } }); if (r.ok) render(await r.json()); } catch (e) { $("overall").textContent = "Control stopped"; }
}
document.addEventListener("click", e => { const b = e.target.closest("[data-act],[data-open]"); if (!b) return;
  if (b.dataset.act) call(b.dataset.act); else window.open(b.dataset.open, "_blank"); });
$("chaosToggle").onclick = () => call("chaos/" + (last && last.chaos.enabled ? "off" : "on"));
$("chaosNow").onclick = () => call("chaos/now");
$("copy").onclick = async () => { if (last) { await navigator.clipboard.writeText(last.app_url); $("copy").textContent = "Copied"; setTimeout(() => $("copy").textContent = "Copy link", 1500); } };
$("quit").onclick = async () => { if (!confirm("Stop SpotBot and close Control?")) return; await call("quit"); document.body.replaceChildren(el("p", { style: "padding:30px" }, "SpotBot is stopped. You can close this window.")); };
refresh(); setInterval(refresh, 3000);
</script></body></html>
"""

if __name__ == "__main__":
    main()
