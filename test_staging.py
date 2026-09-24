"""Staging deployment (#11): what's exposed, what runs as whom, and how it deploys.

The #11 rule "only the ports that must be open are open" is checked here against
the compose files themselves, so a later edit that publishes /dash or the admin
API fails the suite instead of reaching the internet.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin

REPO = Path(__file__).parent


class _ComposeLoader(yaml.SafeLoader):
    pass


_ComposeLoader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node))


def _compose(name: str) -> dict:
    return yaml.load((REPO / name).read_text(encoding="utf-8"), Loader=_ComposeLoader)


BASE = _compose("docker-compose.staging.yml")
DOMAIN = _compose("docker-compose.staging.domain.yml")
SERVICES = BASE["services"]


# ── exposure ────────────────────────────────────────────────────────────────


def test_nothing_is_published_beyond_the_hosts_loopback():
    """Tunnel mode: the share proxy for the tunnel, the admin app for /dash over SSH —
    both on 127.0.0.1 only. Recommend, Ollama and the bot aren't published at all."""
    published = {name: svc["ports"] for name, svc in SERVICES.items() if svc.get("ports")}
    assert published == {"share-proxy": ["127.0.0.1:8080:8080"], "admin": ["127.0.0.1:8010:8010"]}


def test_domain_mode_opens_80_and_443_on_the_proxy_and_nothing_else():
    ports = {name: svc["ports"] for name, svc in DOMAIN["services"].items() if svc.get("ports")}
    assert ports == {"share-proxy": ["80:80", "443:443"]}      # the admin app stays on loopback


def test_secrets_reach_only_the_services_that_use_them():
    holders = {name for name, svc in SERVICES.items() if svc.get("env_file")}
    assert holders == {"admin", "recommend", "bot"}


def test_the_app_containers_dont_run_as_root():
    assert SERVICES["recommend"]["user"] == "10001" and SERVICES["bot"]["user"] == "10001"
    dockerfile = (REPO / "admin" / "Dockerfile").read_text(encoding="utf-8")
    assert "useradd --create-home --uid 10001 spotbot" in dockerfile and "\nUSER spotbot" in dockerfile
    assert "chown -R 10001:10001 /data" in " ".join(SERVICES["data-init"]["command"])


def test_the_bot_talks_to_internal_names_and_needs_a_public_https_address():
    env = SERVICES["bot"]["environment"]
    assert env["INGEST_URL"] == env["ADMIN_URL"] == "http://admin:8010"
    assert env["RECOMMEND_URL"] == "http://recommend:8003"
    assert env["SHARE_BASE_URL"].startswith("${SHARE_BASE_URL:?")      # compose refuses to start without it
    assert env["BOT_CONFIG_FILE"] == "/app/state/channels.json"


def test_captures_survive_a_restart_and_health_is_checked():
    assert SERVICES["admin"]["environment"]["JOB_STORE"] == "sqlite"
    assert "/health" in " ".join(SERVICES["admin"]["healthcheck"]["test"])
    assert SERVICES["bot"]["depends_on"]["admin"]["condition"] == "service_healthy"


def test_every_build_points_at_a_real_dockerfile():
    for name, svc in SERVICES.items():
        build = svc.get("build")
        if build is None:
            continue
        context = REPO / (build if isinstance(build, str) else build["context"])
        dockerfile = context / (build.get("dockerfile", "Dockerfile") if isinstance(build, dict) else "Dockerfile")
        assert dockerfile.exists(), f"{name}: {dockerfile}"


def test_the_admin_image_has_what_capture_needs():
    dockerfile = (REPO / "admin" / "Dockerfile").read_text(encoding="utf-8")
    for needed in ("ffmpeg", "playwright install --with-deps chromium", "PLAYWRIGHT_BROWSERS_PATH",
                   "COPY src/ ./src/", "src.ingestion.serving.admin:app"):
        assert needed in dockerfile
    assert "faster-whisper==" in (REPO / "admin" / "requirements.txt").read_text(encoding="utf-8")


# ── the proxy ───────────────────────────────────────────────────────────────


CADDY = (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")


def test_the_proxy_forwards_two_read_only_routes_and_404s_the_rest():
    assert re.findall(r"reverse_proxy\s+(\S+)", CADDY) == ["admin:8010", "admin:8010"]
    assert re.findall(r"handle\s+(@\w+)", CADDY) == ["@share_page", "@share_events"]
    assert 'respond "Not found" 404' in CADDY
    for matcher, path in (("share_page", "/share"), ("share_events", "/api/events")):
        block = re.search(rf"@{matcher} \{{(.*?)\n\t\}}", CADDY, re.S).group(1)   # up to its own closing brace
        assert "method GET" in block and f"path {path}\n" in block + "\n"
        assert '{query.guild_id} != ""' in block      # the open legacy "" catalog stays private


def test_the_proxy_keeps_the_share_token_out_of_referers_and_its_admin_off():
    assert 'Referrer-Policy "no-referrer"' in CADDY
    assert re.search(r"^\{\s*\n\s*admin off", CADDY, re.M)


def test_the_share_page_itself_sends_no_referrer():
    assert '<meta name="referrer" content="no-referrer"/>' in admin._SHARE_PAGE


# ── /health ─────────────────────────────────────────────────────────────────


def test_health_answers_without_touching_the_catalog(monkeypatch):
    def no_catalog(*args, **kwargs):
        raise AssertionError("/health must not open Chroma")

    monkeypatch.setattr(admin, "_sink_for", no_catalog)
    with TestClient(admin.app) as client:
        r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


# ── scripts/deploy.py ───────────────────────────────────────────────────────


@pytest.fixture()
def deploy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("deploy_script", REPO / "scripts" / "deploy.py")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["deploy_script"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("deploy_script", None)


GOOD_ENV = "DISCORD_TOKEN=tok\nSPOTBOT_SIGNING_KEY=key\nSHARE_BASE_URL=https://spots.example.test\n"
REVIEWED = "Release notes\n\nSecurity-Review: Jordan (Track D)\n"


class FakeHost:
    """Scripted git + docker. `healthy` decides what `compose ps` reports."""

    def __init__(self, tags=None, healthy=True):
        self.tags = tags if tags is not None else {"v0.1.0": REVIEWED, "v0.2.0": REVIEWED}
        self.healthy = healthy
        self.calls: list[list[str]] = []
        self.checked_out = None

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:3] == ["git", "cat-file", "-t"]:
            message = self.tags.get(cmd[3])
            return (0, "tag\n") if message is not None else (128, "fatal: Not a valid object name")
        if cmd[:3] == ["git", "tag", "-l"]:
            return 0, self.tags.get(cmd[-1], "")
        if cmd[:2] == ["git", "checkout"]:
            self.checked_out = cmd[-1]
            return 0, ""
        if "ps" in cmd:
            ok = self.healthy(self.checked_out) if callable(self.healthy) else self.healthy
            rows = [{"Service": "admin", "State": "running", "Health": "healthy" if ok else "unhealthy"},
                    {"Service": "recommend", "State": "running", "Health": "healthy"},
                    {"Service": "bot", "State": "running", "Health": ""},
                    {"Service": "share-proxy", "State": "running", "Health": ""}]
            return 0, "\n".join(json.dumps(r) for r in rows)
        return 0, ""

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def _deployer(module, tmp_path, host, **kwargs):
    (tmp_path / ".env").write_text(kwargs.pop("env", GOOD_ENV), encoding="utf-8")
    ticks = iter(range(0, 10_000, 5))
    return module.Deployer(host, root=tmp_path, wait_s=30, sleep=lambda s: None,
                           clock=lambda: next(ticks), out=lambda *a: None, **kwargs)


def _state(tmp_path):
    return json.loads((tmp_path / "deploy" / "state.json").read_text(encoding="utf-8"))


def test_a_reviewed_tag_deploys_in_order_and_is_recorded(deploy_module, tmp_path):
    host = FakeHost()
    assert _deployer(deploy_module, tmp_path, host).deploy("v0.1.0") == "v0.1.0"
    # git calls by their subcommand; compose calls ("docker compose -f <file> <verb>") by verb
    steps = [" ".join(c[:2]) if c[0] == "git" else c[4] for c in host.calls[:6]]
    assert steps == ["git fetch", "git cat-file", "git tag", "config", "git checkout", "up"]
    assert _state(tmp_path) == {"current": "v0.1.0", "previous": None}
    assert "deployed v0.1.0 (reviewed by Jordan (Track D))" in (tmp_path / "deploy" / "deploys.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("tags", [{"v0.1.0": "Release notes only\n"}, {}])
def test_an_unreviewed_or_unknown_tag_never_deploys(deploy_module, tmp_path, tags):
    host = FakeHost(tags=tags)
    with pytest.raises(deploy_module.DeployError, match="Security-Review"):
        _deployer(deploy_module, tmp_path, host).deploy("v0.1.0")
    assert not host.ran("git", "checkout") and not [c for c in host.calls if "up" in c]


def test_a_lightweight_tag_counts_as_unreviewed(deploy_module, tmp_path):
    host = FakeHost()
    host_call = host.__call__

    def lightweight(cmd):
        return (0, "commit\n") if cmd[:3] == ["git", "cat-file", "-t"] else host_call(cmd)

    with pytest.raises(deploy_module.DeployError, match="Security-Review"):
        _deployer(deploy_module, tmp_path, lightweight).deploy("v0.1.0")


@pytest.mark.parametrize("name", ["main", "latest", "v1", "1.0.0"])
def test_only_release_tags_deploy(deploy_module, tmp_path, name):
    with pytest.raises(deploy_module.DeployError, match="release tag"):
        _deployer(deploy_module, tmp_path, FakeHost()).deploy(name)


@pytest.mark.parametrize("env, problem", [
    ("DISCORD_TOKEN=SECRET-TOKEN-123\nSPOTBOT_SIGNING_KEY=\nSHARE_BASE_URL=https://x.test\n",
     "SPOTBOT_SIGNING_KEY is empty"),
    ("DISCORD_TOKEN=SECRET-TOKEN-123\nSPOTBOT_SIGNING_KEY=SECRET-KEY-456\nSHARE_BASE_URL=http://x.test\n",
     "must start with https://"),
])
def test_env_problems_stop_the_deploy_without_printing_secrets(deploy_module, tmp_path, env, problem):
    with pytest.raises(deploy_module.DeployError) as exc:
        _deployer(deploy_module, tmp_path, FakeHost(), env=env).deploy("v0.1.0")
    message = str(exc.value)
    assert problem in message and "SECRET-TOKEN-123" not in message and "SECRET-KEY-456" not in message


def test_an_unhealthy_release_rolls_itself_back(deploy_module, tmp_path):
    host = FakeHost(healthy=lambda tag: tag != "v0.2.0")
    deployer = _deployer(deploy_module, tmp_path, host)
    deployer.deploy("v0.1.0")
    with pytest.raises(deploy_module.DeployError, match="rolled back to v0.1.0"):
        deployer.deploy("v0.2.0")
    assert host.checked_out == "v0.1.0"
    assert _state(tmp_path) == {"current": "v0.1.0", "previous": None}      # the good one is still live
    log = (tmp_path / "deploy" / "deploys.log").read_text(encoding="utf-8")
    assert "FAILED v0.2.0" in log and "rolled back to v0.1.0" in log


def test_rollback_goes_to_the_previous_tag_and_can_go_forward_again(deploy_module, tmp_path):
    host = FakeHost()
    deployer = _deployer(deploy_module, tmp_path, host)
    deployer.deploy("v0.1.0")
    deployer.deploy("v0.2.0")
    assert deployer.rollback() == "v0.1.0" and host.checked_out == "v0.1.0"
    assert _state(tmp_path) == {"current": "v0.1.0", "previous": "v0.2.0"}


def test_rollback_with_nothing_before_says_so(deploy_module, tmp_path):
    with pytest.raises(deploy_module.DeployError, match="no previous deploy"):
        _deployer(deploy_module, tmp_path, FakeHost()).rollback()


def test_skipping_review_is_possible_but_logged_with_the_reason(deploy_module, tmp_path):
    host = FakeHost(tags={"v0.1.0": "no review line\n"})
    _deployer(deploy_module, tmp_path, host).deploy("v0.1.0", skip_review="hotfix for a live outage")
    log = (tmp_path / "deploy" / "deploys.log").read_text(encoding="utf-8")
    assert "REVIEW SKIPPED: hotfix for a live outage" in log


def test_dry_run_checks_everything_and_changes_nothing(deploy_module, tmp_path):
    host = FakeHost()
    _deployer(deploy_module, tmp_path, host, dry_run=True).deploy("v0.1.0")
    assert not host.ran("git", "checkout") and not [c for c in host.calls if "up" in c]
    assert host.ran("git", "cat-file") and [c for c in host.calls if "config" in c]
    assert not (tmp_path / "deploy").exists()


def test_compose_ps_output_is_read_in_both_formats(deploy_module):
    rows = [{"Service": "admin", "Health": "healthy"}]
    for out in (json.dumps(rows), json.dumps(rows[0])):
        states = deploy_module.service_states(lambda cmd: (0, out), ["docker", "compose"])
        assert states["admin"]["Health"] == "healthy"
