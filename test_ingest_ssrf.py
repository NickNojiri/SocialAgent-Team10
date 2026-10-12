"""SSRF guard at the capture API (THREAT_MODEL T3, feature #12).

/api/ingest and /api/jobs must refuse a link that names an internal host with a
400, before any browser or queue work. Offline: nothing here is fetched.
"""

import pytest
from fastapi.testclient import TestClient

from src.ingestion.serving.admin import _internal_target
from src.ingestion.serving.admin import app as admin_app

INTERNAL = [
    "http://127.0.0.1/",
    "http://127.0.0.1:8010/api/catalog",
    "http://localhost/",
    "http://LOCALHOST./",
    "http://admin.localhost/",
    "http://printer.local/",
    "http://metadata.google.internal/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://100.64.0.1/",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[fd00::1]/",
    "http://[fe80::1]/",
    "http://2130706433/",       # 127.0.0.1 as one integer
    "http://0x7f.0.0.1/",       # hex octet
    "http://127.1/",            # short form
    "http://017700000001/",     # octal
    "http://224.0.0.1/",        # multicast
    "https://www.instagram.com@127.0.0.1/reel/x/",
    "https://user:pw@www.instagram.com/reel/x/",
    "https://www.instagram.com:8443/reel/x/",
    "http://[::1/",
]

PUBLIC = [
    "https://www.instagram.com/reel/DZIPn-ppdU4/",
    "https://www.instagram.com:443/reel/DZIPn-ppdU4/",
    "https://www.tiktok.com/@someone/video/123",
    "https://x.test/1",
    "http://8.8.8.8/",
    "https://cafe.de/",
]


@pytest.mark.parametrize("url", INTERNAL)
def test_internal_targets_are_named(url):
    assert _internal_target(url)


@pytest.mark.parametrize("url", PUBLIC)
def test_public_targets_pass(url):
    assert _internal_target(url) is None


@pytest.mark.parametrize("path", ["/api/ingest", "/api/jobs"])
@pytest.mark.parametrize("url", INTERNAL)
def test_capture_endpoints_refuse_internal_targets(path, url):
    client = TestClient(admin_app)
    resp = client.post(path, json={"urls": [url]})
    assert resp.status_code == 400
    assert "refusing to capture" in resp.json()["detail"]


@pytest.mark.parametrize("path", ["/api/ingest", "/api/jobs"])
def test_one_internal_link_refuses_the_whole_request(path):
    client = TestClient(admin_app)
    urls = ["https://www.instagram.com/reel/DZIPn-ppdU4/", "http://127.0.0.1:8010/"]
    resp = client.post(path, json={"urls": urls})
    assert resp.status_code == 400
