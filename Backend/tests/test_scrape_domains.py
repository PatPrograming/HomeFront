"""Per-agent web.scrape domain allowlist (argument-level authorization)."""
import socket
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import ADMIN

from identity_gateway.dynamo_repo import agent_from_item, agent_to_item  # noqa: E402
from identity_gateway.runner import system_prompt  # noqa: E402
from identity_gateway.store import (  # noqa: E402
    AgentRecord,
    InvalidScrapeDomainsError,
    normalize_scrape_domains,
    widens_scrape_policy,
)
from mcp_tools import handlers  # noqa: E402

client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")


def _create(**body):
    r = client.post(
        "/api/v1/agents",
        json={"agent_id": f"agent-dom-{uuid.uuid4().hex[:8]}", "name": "Scraper", "agency": "Dept.", **body},
    )
    return r


def _token(agent_id):
    return {"X-Agent-Token": client.post('/api/v1/auth/token', json={'agent_id': agent_id}).json()['access_token']}


def _scrape(headers, url):
    return client.post("/api/v1/agents/tool-call", json={"tool": "web.scrape", "arguments": {"url": url}}, headers=headers)


# --- matching -----------------------------------------------------------------


@pytest.mark.parametrize(
    "host, patterns, ok",
    [
        ("sam.gov", ["*.gov"], True),
        ("www.sam.gov", ["*.gov"], True),
        ("SAM.GOV.", ["*.gov"], True),
        ("evilgov", ["*.gov"], False),
        ("sam.gov.evil.com", ["*.gov"], False),
        ("example.com", ["*.gov"], False),
        ("sam.gov", ["sam.gov"], True),
        ("www.sam.gov", ["sam.gov"], False),
        ("notsam.gov", ["*.sam.gov"], False),
        ("api.sam.gov", ["*.sam.gov"], True),
        ("sam.gov", ["*.sam.gov"], True),
        ("", ["*.gov"], False),
        ("example.org", ["*.gov", "example.org"], True),
    ],
)
def test_host_matching(host, patterns, ok):
    assert handlers.host_matches_domains(host, patterns) is ok


def test_normalize_scrape_domains():
    assert normalize_scrape_domains([" *.GOV ", "sam.gov.", "", "*.gov"]) == ["*.gov", "sam.gov"]
    assert normalize_scrape_domains(None) == []


@pytest.mark.parametrize("bad", ["*", "*.", "http://sam.gov", "sam.gov/path", "-a.gov", "a..gov", "**.gov", "10.0.0.1", "sam gov", "*.*.gov"])
def test_normalize_rejects_bad_patterns(bad):
    with pytest.raises(InvalidScrapeDomainsError):
        normalize_scrape_domains([bad])


def test_widening():
    assert widens_scrape_policy([], ["*.gov"]) is False
    assert widens_scrape_policy(["*.gov"], []) is True
    assert widens_scrape_policy(["*.gov"], ["*.gov", "a.org"]) is True
    assert widens_scrape_policy(["*.gov", "a.org"], ["*.gov"]) is False


# --- API ---------------------------------------------------------------------------


def test_market_research_defaults_to_gov_only():
    body = _create(kind="market_research").json()
    assert body["scrape_domains"] == ["*.gov"]
    types = {t["kind"]: t for t in client.get("/api/v1/agent-types").json()}
    assert types["market_research"]["default_scrape_domains"] == ["*.gov"]
    assert types["research"]["default_scrape_domains"] == []


def test_explicit_empty_list_overrides_type_default():
    assert _create(kind="market_research", scrape_domains=[]).json()["scrape_domains"] == []


def test_custom_agent_unrestricted_by_default():
    assert _create(tool_grants=["web.scrape"]).json()["scrape_domains"] == []


def test_create_rejects_invalid_patterns():
    r = _create(scrape_domains=["*.gov", "http://bad"])
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_scrape_domains"
    assert r.json()["detail"]["invalid"] == ["http://bad"]


def test_patch_scrape_domains_validates_and_audits():
    agent = _create(tool_grants=["web.scrape"]).json()
    aid = agent["agent_id"]
    bad = client.patch(f"/api/v1/agents/{aid}", json={"scrape_domains": ["*"], "name": "Renamed"})
    assert bad.status_code == 400
    assert client.get(f"/api/v1/agents/{aid}").json()["name"] == "Scraper"  # nothing changed

    ok = client.patch(f"/api/v1/agents/{aid}", json={"scrape_domains": ["*.gov"]}).json()
    assert ok["scrape_domains"] == ["*.gov"]
    events = client.get("/api/v1/audit", params={"agent_id": aid, "type": "agent_updated"}).json()
    assert events[0]["fields"] == ["scrape_domains"]

    # null is ignored; [] removes the restriction.
    assert client.patch(f"/api/v1/agents/{aid}", json={"scrape_domains": None}).json()["scrape_domains"] == ["*.gov"]
    assert client.patch(f"/api/v1/agents/{aid}", json={"scrape_domains": []}).json()["scrape_domains"] == []


def test_narrowing_keeps_tokens_widening_invalidates_them():
    agent = _create(tool_grants=["web.scrape"]).json()
    aid, v0 = agent["agent_id"], agent["token_version"]
    narrowed = client.patch(f"/api/v1/agents/{aid}", json={"scrape_domains": ["*.gov"]}).json()
    assert narrowed["token_version"] == v0
    widened = client.patch(f"/api/v1/agents/{aid}", json={"scrape_domains": ["*.gov", "example.org"]}).json()
    assert widened["token_version"] == v0 + 1


# --- enforcement ---------------------------------------------------------------------


def test_mcp_call_denies_scrape_outside_allowlist_and_audits():
    agent = _create(tool_grants=["web.scrape"], scrape_domains=["*.gov"]).json()
    headers = _token(agent["agent_id"])

    assert _scrape(headers, "https://www.sam.gov/content").status_code == 200
    r = _scrape(headers, "https://attacker.example.com/payload")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "domain_not_allowed"
    assert detail["host"] == "attacker.example.com" and detail["allowed_domains"] == ["*.gov"]
    events = client.get("/api/v1/audit", params={"agent_id": agent["agent_id"], "decision": "deny"}).json()
    [event] = [e for e in events if e["id"] == detail["audit_id"]]
    assert event["reason"] == "domain_not_allowed"
    assert "AML.T0053" in event["atlas"]["technique_ids"]


def test_url_without_host_is_denied_under_policy():
    agent = _create(tool_grants=["web.scrape"], scrape_domains=["*.gov"]).json()
    assert _scrape(_token(agent["agent_id"]), "not a url").json()["detail"]["error"] == "domain_not_allowed"


def test_narrowing_mid_session_applies_to_next_call():
    agent = _create(tool_grants=["web.scrape"]).json()
    headers = _token(agent["agent_id"])
    assert _scrape(headers, "https://example.com/").status_code == 200
    client.patch(f"/api/v1/agents/{agent['agent_id']}", json={"scrape_domains": ["*.gov"]})
    assert _scrape(headers, "https://example.com/").status_code == 403


def test_agent_cannot_widen_policy_through_arguments():
    agent = _create(tool_grants=["web.scrape"], scrape_domains=["*.gov"]).json()
    r = client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "web.scrape", "arguments": {"url": "https://example.com/", "allowed_domains": ["*.com"]}},
        headers=_token(agent["agent_id"]),
    )
    assert r.status_code == 403


def test_live_redirect_outside_allowlist_is_blocked(monkeypatch):
    import email.message
    import io
    import urllib.request
    import urllib.response

    monkeypatch.setenv("HOMEFRONT_LIVE_SCRAPE", "1")
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))])

    class Web(urllib.request.HTTPSHandler):
        def https_open(self, req):
            headers = email.message.Message()
            headers["Location"] = "https://evil.example.com/"
            resp = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
            resp.msg = "Found"
            return resp

    monkeypatch.setattr(
        handlers,
        "_scrape_opener",
        lambda allowed=None: urllib.request.build_opener(handlers._SafeRedirectHandler(allowed), Web),
    )
    out = handlers.handle_web_scrape({"url": "https://www.sam.gov/", "allowed_domains": ["*.gov"]})
    assert out["status"] == "denied" and "allowed domains" in out["reason"]


# --- persistence and prompt ------------------------------------------------------------


def test_scrape_domains_round_trip_dynamo_item():
    agent = AgentRecord("agent-dom-x", "A", "B", {"web.scrape"}, scrape_domains=["*.gov"])
    assert agent_from_item(agent_to_item(agent)).scrape_domains == ["*.gov"]
    legacy = agent_to_item(AgentRecord("agent-dom-y", "A", "B", set()))
    del legacy["scrape_domains"]
    assert agent_from_item(legacy).scrape_domains == []


def test_dynamo_repository_persists_scrape_domains():
    from app.core.dynamodb import table
    from identity_gateway.dynamo_repo import DynamoAgentRepository

    repo = DynamoAgentRepository(table("agents"))
    aid = f"agent-dom-{uuid.uuid4().hex[:8]}"
    repo.insert(AgentRecord(aid, "A", "B", {"web.scrape"}, scrape_domains=["*.gov", "sam.gov"]))
    assert repo.get(aid).scrape_domains == ["*.gov", "sam.gov"]


def test_system_prompt_mentions_allowed_domains():
    agent = AgentRecord("agent-dom-z", "A", "B", {"web.scrape"}, scrape_domains=["*.gov"])
    prompt = system_prompt(agent)
    assert "can only fetch these domains: *.gov" in prompt
    soft = system_prompt(AgentRecord("agent-dom-w", "A", "B", {"web.scrape"}))
    assert "can only fetch these domains" not in soft
    assert "Prefer *.gov" in soft
    assert "UI will warn" in soft
