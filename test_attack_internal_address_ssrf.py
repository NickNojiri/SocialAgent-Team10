"""Internal-address and redirect SSRF probes; all transports are local fakes."""

import socket
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

import src.ingestion.browser.ig_embed as ig_embed
from src.ingestion.browser.session_manager import SocialSessionManager
import src.ingestion.pipeline.transcriber as transcriber_module
from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.transcriber import Transcriber
from src.ingestion.serving import admin

PRIVATE_URLS = [
    "http://127.0.0.1/admin",
    "http://localhost/admin",
    "http://10.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/admin",
]


@pytest.mark.parametrize("url", PRIVATE_URLS)
@pytest.mark.parametrize("path", ["/api/ingest", "/api/jobs"])
def test_capture_endpoints_reject_internal_addresses(monkeypatch, url, path):
    """Pasted internal URLs must be refused before capture or queue work starts."""
    from fastapi.testclient import TestClient

    work = []

    class NeverRunJobs:
        def find_active(self, guild_id, urls):
            return None

        def submit(self, urls, guild_id):
            work.append(("queue", urls))
            return SimpleNamespace(id="local-job", state="queued")

    async def never_run_ingest(urls, guild_id):
        work.append(("pipeline", urls))
        return {"added": 0, "rejected": 0, "unreadable": 0, "events": [], "log": []}

    monkeypatch.setattr(admin, "_jobs", NeverRunJobs())
    monkeypatch.setattr(admin, "_run_ingest", never_run_ingest)
    client = TestClient(admin.app)
    response = client.post(path, json={"urls": [url]})
    client.close()

    assert response.status_code == 400, response.text
    assert work == []


class LocalBrowser:
    """A browser double whose navigation always fails instead of using a network."""

    def __init__(self, navigation_attempts):
        self.navigation_attempts = navigation_attempts

    async def new_context(self, **kwargs):
        return LocalBrowserContext(self.navigation_attempts)


class LocalBrowserContext:
    def __init__(self, navigation_attempts):
        self.navigation_attempts = navigation_attempts

    async def new_page(self):
        return self

    async def goto(self, url, **kwargs):
        self.navigation_attempts.append(url)
        raise PlaywrightError("navigation blocked by offline test double")

    async def close(self):
        return None


@pytest.mark.parametrize("url", PRIVATE_URLS)
@pytest.mark.asyncio
async def test_browser_fetch_rejects_internal_addresses_before_navigation(url):
    """The browser fetch guard must stop private URLs before its fake browser sees them."""
    navigation_attempts = []
    manager = SocialSessionManager(IngestionSettings(per_domain_delay_s=0))
    manager._browser = LocalBrowser(navigation_attempts)

    await manager.fetch(url)

    assert navigation_attempts == []


@pytest.mark.asyncio
async def test_browser_fetch_rejects_hostname_that_resolves_to_private_ip(monkeypatch):
    """DNS is mocked so testing a private resolution never contacts a resolver."""
    calls = []

    def fake_getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    navigation_attempts = []
    manager = SocialSessionManager(IngestionSettings(per_domain_delay_s=0))
    manager._browser = LocalBrowser(navigation_attempts)

    await manager.fetch("https://spot.example/reel/abc")

    assert calls == ["spot.example"]
    assert navigation_attempts == []


@pytest.mark.asyncio
async def test_embed_fallback_does_not_follow_redirect_to_internal_address(monkeypatch):
    """An in-memory 302 must not make the client request its internal Location."""
    initial_url = "https://www.instagram.com/p/ABC123/embed/captioned/"
    internal_url = "http://127.0.0.1/admin"
    requested_urls = []

    def fake_server(request):
        requested_urls.append(str(request.url))
        if str(request.url) == initial_url:
            return ig_embed.httpx.Response(
                302,
                headers={"Location": internal_url},
                request=request,
            )
        return ig_embed.httpx.Response(500, request=request)

    real_async_client = ig_embed.httpx.AsyncClient
    transport = ig_embed.httpx.MockTransport(fake_server)

    def make_local_client(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(ig_embed.httpx, "AsyncClient", make_local_client)
    await ig_embed.try_embed_fallback("https://www.instagram.com/reel/ABC123/")

    assert requested_urls == [initial_url]
    assert internal_url not in requested_urls


def test_video_download_does_not_follow_redirect_to_internal_address(monkeypatch):
    """An in-memory 302 must not make the downloader request its internal Location."""
    initial_url = "https://media.example/video.mp4"
    internal_url = "http://169.254.169.254/latest/meta-data/"
    requested_urls = []

    def fake_server(request):
        requested_urls.append(str(request.url))
        if str(request.url) == initial_url:
            return transcriber_module.httpx.Response(
                302,
                headers={"Location": internal_url},
                request=request,
            )
        return transcriber_module.httpx.Response(500, request=request)

    real_client = transcriber_module.httpx.Client
    transport = transcriber_module.httpx.MockTransport(fake_server)

    @contextmanager
    def local_stream(method, url, **kwargs):
        follow_redirects = kwargs.pop("follow_redirects", False)
        client = real_client(
            transport=transport,
            follow_redirects=follow_redirects,
            **kwargs,
        )
        try:
            with client.stream(method, url) as response:
                yield response
        finally:
            client.close()

    monkeypatch.setattr(transcriber_module.httpx, "stream", local_stream)
    assert Transcriber(IngestionSettings())._download(initial_url) is None

    assert requested_urls == [initial_url]
    assert internal_url not in requested_urls
