"""web.scrape SSRF guard: only public http(s) hosts, re-checked on redirects."""
import email.message
import io
import socket
import urllib.request
import urllib.response

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import ADMIN

from mcp_tools import handlers  # noqa: E402

client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")

PUBLIC_IP = "93.184.215.14"


def _fake_dns(mapping):
    def getaddrinfo(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]

    return getaddrinfo


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("HOMEFRONT_LIVE_SCRAPE", "1")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://localhost:8080/api/v1/agents",
        "http://127.0.0.1/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://2130706433/",  # 127.0.0.1 as a decimal
        "http://0x7f.1/",
        "http://0.0.0.0/",
        "http://app.localhost/",
        "file:///etc/passwd",
        "ftp://example.gov/x",
        "gopher://example.gov/",
        "https://user:pw@example.gov/",
        "not a url",
    ],
)
def test_simulated_scrape_refuses_non_public_urls(url, monkeypatch):
    monkeypatch.delenv("HOMEFRONT_LIVE_SCRAPE", raising=False)
    out = handlers.handle_web_scrape({"url": url})
    assert out["status"] == "denied", out
    assert out["error"] == "url_not_allowed"
    assert "AML.M0033" in out["atlas_note"]


def test_simulated_scrape_allows_public_hostname_without_dns(monkeypatch):
    monkeypatch.delenv("HOMEFRONT_LIVE_SCRAPE", raising=False)

    def no_dns(*_a, **_k):
        raise AssertionError("simulated scrape must not touch DNS")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    out = handlers.handle_web_scrape({"url": "https://www.example.gov/notice"})
    assert out["status"] == "ok" and out["simulated"] is True


def test_live_scrape_blocks_hostname_resolving_to_private_ip(live, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"internal.example.gov": "10.1.2.3"}))
    monkeypatch.setattr(handlers, "_scrape_opener", lambda *_: pytest.fail("must not fetch"))
    out = handlers.handle_web_scrape({"url": "https://internal.example.gov/"})
    assert out["status"] == "denied"
    assert "10.1.2.3" in out["reason"]


def test_live_scrape_blocks_metadata_ip_without_fetching(live, monkeypatch):
    monkeypatch.setattr(handlers, "_scrape_opener", lambda *_: pytest.fail("must not fetch"))
    out = handlers.handle_web_scrape({"url": "http://169.254.169.254/latest/meta-data/"})
    assert out["status"] == "denied" and out["simulated"] is False


def test_live_scrape_unresolvable_host_is_denied(live, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({}))
    out = handlers.handle_web_scrape({"url": "https://nope.invalid/"})
    assert out["status"] == "denied" and "resolve" in out["reason"]


class _FakeWeb(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Serves canned responses by URL instead of opening sockets."""

    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.fetched = []

    def _serve(self, req):
        url = req.full_url
        self.fetched.append(url)
        status, location, body = self.pages[url]
        headers = email.message.Message()
        if location:
            headers["Location"] = location
        resp = urllib.response.addinfourl(io.BytesIO(body), headers, url, status)
        resp.msg = "Found" if location else "OK"
        return resp

    http_open = _serve
    https_open = _serve


def _install_web(monkeypatch, pages):
    web = _FakeWeb(pages)
    monkeypatch.setattr(
        handlers,
        "_scrape_opener",
        lambda allowed_domains=None: urllib.request.build_opener(
            handlers._SafeRedirectHandler(allowed_domains), web
        ),
    )
    return web


def test_live_scrape_fetches_public_host(live, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"www.example.gov": PUBLIC_IP}))
    web = _install_web(monkeypatch, {"https://www.example.gov/": (200, None, b"<p>Hello</p>")})
    out = handlers.handle_web_scrape({"url": "https://www.example.gov/"})
    assert out["status"] == "ok" and "Hello" in out["content_preview"]
    assert web.fetched == ["https://www.example.gov/"]


def test_live_scrape_blocks_redirect_to_metadata(live, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"www.example.gov": PUBLIC_IP}))
    web = _install_web(
        monkeypatch,
        {"https://www.example.gov/": (302, "http://169.254.169.254/latest/meta-data/", b"")},
    )
    out = handlers.handle_web_scrape({"url": "https://www.example.gov/"})
    assert out["status"] == "denied"
    assert out["reason"].startswith("redirect blocked")
    assert web.fetched == ["https://www.example.gov/"]  # never followed


def test_live_scrape_blocks_redirect_to_host_resolving_private(live, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_dns({"www.example.gov": PUBLIC_IP, "evil.example.com": "192.168.0.10"}),
    )
    _install_web(monkeypatch, {"https://www.example.gov/": (301, "https://evil.example.com/", b"")})
    out = handlers.handle_web_scrape({"url": "https://www.example.gov/"})
    assert out["status"] == "denied" and "192.168.0.10" in out["reason"]


def test_live_scrape_follows_redirect_to_public_host(live, monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"a.example.gov": PUBLIC_IP, "b.example.gov": PUBLIC_IP})
    )
    web = _install_web(
        monkeypatch,
        {
            "https://a.example.gov/": (302, "https://b.example.gov/page", b""),
            "https://b.example.gov/page": (200, None, b"final"),
        },
    )
    out = handlers.handle_web_scrape({"url": "https://a.example.gov/"})
    assert out["status"] == "ok" and out["content_preview"] == "final"
    assert web.fetched == ["https://a.example.gov/", "https://b.example.gov/page"]


def test_ssrf_denial_through_gateway(monkeypatch):
    monkeypatch.delenv("HOMEFRONT_LIVE_SCRAPE", raising=False)
    token = client.post("/api/v1/auth/token", json={"agent_id": "agent-research-01"}).json()["access_token"]
    r = client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "web.scrape", "arguments": {"url": "http://169.254.169.254/"}},
        headers={"X-Agent-Token": token},
    )
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "denied"
