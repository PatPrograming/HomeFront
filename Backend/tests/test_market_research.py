"""market_research agent type and its tools: usaspending.search,
sam.entity_lookup and report.save."""
import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import auth, ADMIN

from identity_gateway import agents_api as gateway  # noqa: E402
from identity_gateway.agent_types import AGENT_TYPES, disallowed_tools  # noqa: E402
from identity_gateway.reports import (  # noqa: E402
    DynamoReportRepository,
    MemoryReportRepository,
    ReportRecord,
)
from mcp_tools import handlers  # noqa: E402
from mcp_tools.registry import TOOL_REGISTRY  # noqa: E402

from app.core.dynamodb import table  # noqa: E402

client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")

ALICE = "00000000-0000-0000-0000-0000000a11ce"
BOB = "00000000-0000-0000-0000-000000000b0b"
OUTBOUND = {"email.send", "email.send_list", "social.post", "social.reply"}
NEW_TOOLS = ("usaspending.search", "sam.entity_lookup", "report.save")


def _create(headers=None, **body):
    r = client.post(
        "/api/v1/agents",
        json={
            "agent_id": f"agent-mr-{uuid.uuid4().hex[:8]}",
            "name": "Market Research",
            "agency": "Dept. of Procurement",
            "kind": "market_research",
            **body,
        },
        headers=headers or {},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _token(agent_id, headers=None):
    r = client.post("/api/v1/auth/token", json={"agent_id": agent_id}, headers=headers or {})
    return {"X-Agent-Token": r.json()['access_token']}


def _call(token_headers, tool, arguments):
    return client.post("/api/v1/agents/tool-call", json={"tool": tool, "arguments": arguments}, headers=token_headers)


# --- type -------------------------------------------------------------------


def test_market_research_type_catalog():
    by_kind = {t["kind"]: t for t in client.get("/api/v1/agent-types").json()}
    t = by_kind["market_research"]
    assert t["label"] == "Procurement market research"
    assert t["allowed_tools"] == sorted(
        ["web.scrape", "datagov.search", "usaspending.search", "sam.entity_lookup", "report.save"]
    )
    assert "report.save" in t["default_tools"]
    assert 20 <= t["default_max_steps"] <= 30
    assert "report_save" in t["default_instructions"]


def test_market_research_never_holds_outbound_tools():
    assert set(disallowed_tools("market_research", OUTBOUND)) == OUTBOUND


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_new_tool_schemas_are_strict(name):
    schema = TOOL_REGISTRY[name].input_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])


def test_new_tool_atlas_ids_exist_in_pack():
    for name in NEW_TOOLS:
        tool = TOOL_REGISTRY[name]
        for tid in tool.atlas_techniques:
            assert gateway.enricher.get_technique(tid), tid
        for mid in tool.atlas_mitigations:
            assert gateway.enricher.get_mitigation(mid), mid


def test_grant_email_send_to_market_research_is_denied():
    agent = _create()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/grants/email.send")
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_not_allowed_for_kind"


def test_mcp_call_enforces_ceiling_for_market_research():
    agent = _create()
    gateway.store.grant_tool(agent["agent_id"], "email.send")  # written around the API
    r = _call(_token(agent["agent_id"]), "email.send", {"to": "a@b.gov", "subject": "s", "body": "b"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_not_allowed_for_kind"


# --- usaspending.search -----------------------------------------------------


def test_usaspending_simulated(monkeypatch):
    monkeypatch.delenv("HOMEFRONT_LIVE_USASPENDING", raising=False)
    out = handlers.handle_usaspending_search({"keywords": "body worn cameras", "fiscal_year": 2025})
    assert out["status"] == "ok" and out["simulated"] is True and out["untrusted"] is True
    assert out["results"] and {"award_id", "recipient", "recipient_uei", "amount"} <= set(out["results"][0])


@pytest.mark.parametrize("args", [{"keywords": "ab", "fiscal_year": 0}, {"keywords": "drones", "fiscal_year": 1990}])
def test_usaspending_rejects_bad_args(args):
    assert handlers.handle_usaspending_search(args)["status"] == "error"


def test_fiscal_year_period():
    from datetime import date

    assert handlers._fiscal_year_period(2025) == [{"start_date": "2024-10-01", "end_date": "2025-09-30"}]
    # November 2026 is in FY2027; "last five" = FY2023..FY2027.
    assert handlers._fiscal_year_period(0, today=date(2026, 11, 7)) == [
        {"start_date": "2022-10-01", "end_date": "2027-09-30"}
    ]


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_usaspending_live_posts_and_normalizes(monkeypatch):
    monkeypatch.setenv("HOMEFRONT_LIVE_USASPENDING", "1")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["method"] = req.full_url, req.get_method()
        seen["body"] = json.loads(req.data)
        return _Resp(
            json.dumps(
                {
                    "results": [
                        {
                            "Award ID": "47QTCA20D001",
                            "Recipient Name": "ACME CORP",
                            "Recipient UEI": "ABCDEFGHJK12",
                            "Award Amount": 99.5,
                            "Awarding Agency": "General Services Administration",
                            "Description": "CAMERAS",
                            "generated_internal_id": "CONT_IDV_47QTCA20D001_4732",
                        }
                    ],
                    "page_metadata": {"page": 1, "total": 1},
                }
            ).encode()
        )

    monkeypatch.setattr(handlers.urllib.request, "urlopen", fake_urlopen)
    out = handlers.handle_usaspending_search(
        {
            "keyword": "cameras",  # alias of keywords
            "fiscal_year": 2024,
            "awarding_agency": "General Services Administration",
            "limit": 5,
        }
    )
    assert seen["url"] == handlers.USASPENDING_AWARD_SEARCH_URL and seen["method"] == "POST"
    assert seen["body"]["filters"]["keywords"] == ["cameras"]
    assert seen["body"]["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert seen["body"]["filters"]["agencies"] == [
        {"type": "awarding", "tier": "toptier", "name": "General Services Administration"}
    ]
    assert seen["body"]["limit"] == 5
    assert out["simulated"] is False
    assert out["page_metadata"] == {"page": 1, "total": 1}
    [award] = out["results"]
    assert award["recipient_uei"] == "ABCDEFGHJK12"
    assert award["url"] == "https://www.usaspending.gov/award/CONT_IDV_47QTCA20D001_4732"


def test_usaspending_live_smoke_against_public_api():
    """Hit the real USAspending endpoint (skipped if offline / flag off in CI without network)."""
    import os

    if os.environ.get("HOMEFRONT_SKIP_LIVE_NETWORK") == "1":
        import pytest

        pytest.skip("network live checks disabled")
    out = handlers.handle_usaspending_search(
        {"keywords": "body worn cameras", "fiscal_year": 2024, "limit": 2}
    )
    # Force live for this smoke regardless of caller env.
    # Re-run with flag on if the first call was simulated.
    if out.get("simulated"):
        os.environ["HOMEFRONT_LIVE_USASPENDING"] = "1"
        out = handlers.handle_usaspending_search(
            {"keywords": "body worn cameras", "fiscal_year": 2024, "limit": 2}
        )
    assert out["status"] == "ok" and out["simulated"] is False
    assert out["result_count"] >= 1
    assert out["results"][0]["award_id"]


# --- sam.entity_lookup ------------------------------------------------------

SECRET = "sam-test-key-1234567890"


def test_sam_simulated_without_flag(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", SECRET)
    monkeypatch.delenv("HOMEFRONT_LIVE_SAM", raising=False)
    out = handlers.handle_sam_entity_lookup({"name": "acme"})
    assert out["status"] == "ok" and out["simulated"] is True
    assert out["results"][0]["uei"]
    assert SECRET not in json.dumps(out)


def test_sam_live_uses_header_key_and_never_returns_it(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", SECRET)
    monkeypatch.setenv("HOMEFRONT_LIVE_SAM", "1")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["key"] = req.get_header("X-api-key")
        return _Resp(
            json.dumps(
                {
                    "totalRecords": 1,
                    "entityData": [
                        {
                            "entityRegistration": {
                                "legalBusinessName": "ACME CORP",
                                "ueiSAM": "ABCDEFGHJK12",
                                "cageCode": "1ABC2",
                                "registrationStatus": "Active",
                            },
                            "coreData": {
                                "physicalAddress": {"city": "Reston", "stateOrProvinceCode": "VA"},
                                "businessTypes": {
                                    "businessTypeList": [{"businessTypeDesc": "Woman Owned Business"}],
                                    "sbaBusinessTypeList": [{"sbaBusinessTypeDesc": "8(a) Program"}],
                                },
                            },
                            # A hostile echo of the key must still be scrubbed.
                            "echo": SECRET,
                        }
                    ],
                    "links": {"selfLink": f"https://api.sam.gov/...?api_key={SECRET}"},
                }
            ).encode()
        )

    monkeypatch.setattr(handlers.urllib.request, "urlopen", fake_urlopen)
    out = handlers.handle_sam_entity_lookup({"name": "acme"})
    assert seen["key"] == SECRET and SECRET not in seen["url"]
    assert out["simulated"] is False
    [entity] = out["results"]
    assert entity["uei"] == "ABCDEFGHJK12" and entity["sba_business_types"] == ["8(a) Program"]
    assert SECRET not in json.dumps(out)


def test_sam_live_error_scrubs_key(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", SECRET)
    monkeypatch.setenv("HOMEFRONT_LIVE_SAM", "1")

    def boom(req, timeout=None):
        raise handlers.urllib.error.URLError(f"failed {req.full_url} key={SECRET}")

    monkeypatch.setattr(handlers.urllib.request, "urlopen", boom)
    out = handlers.handle_sam_entity_lookup({"name": "acme"})
    assert out["status"] == "error" and SECRET not in json.dumps(out)


def test_sam_through_gateway_hides_key(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", SECRET)
    agent = _create()
    body = _call(_token(agent["agent_id"]), "sam.entity_lookup", {"name": "acme"}).json()
    assert body["decision"] == "allow"
    assert body["result"]["credentials_exposed"] is False
    assert body["untrusted_input_atlas"]["technique_ids"] == ["AML.T0051.001"]
    assert SECRET not in json.dumps(body)


# --- report.save --------------------------------------------------------------


def test_report_save_and_list_owner_scoped():
    alice = auth(ALICE)
    agent = _create(headers=alice)
    aid = agent["agent_id"]
    tok = _token(aid, headers=alice)

    r = _call({**alice, **tok}, "report.save", {"title": "Market research: cameras", "body": "## Overview\nThree vendors."})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["status"] == "saved" and result["report_id"].startswith("rpt_")

    reports = client.get(f"/api/v1/agents/{aid}/reports", headers=alice).json()
    assert [rep["report_id"] for rep in reports] == [result["report_id"]]
    assert reports[0]["body"].startswith("## Overview")
    assert reports[0]["agent_id"] == aid and reports[0]["trace_id"]

    assert client.get(f"/api/v1/agents/{aid}/reports", headers=auth(BOB)).status_code == 404
    assert client.get(f"/api/v1/agents/{aid}/reports").status_code == 404  # anonymous


def test_report_save_ignores_spoofed_agent_id():
    victim = _create()
    attacker = _create()
    r = _call(
        _token(attacker["agent_id"]),
        "report.save",
        {"title": "x", "body": "y", "agent_id": victim["agent_id"]},
    )
    assert r.json()["result"]["status"] == "saved"
    assert client.get(f"/api/v1/agents/{victim['agent_id']}/reports").json() == []
    assert len(client.get(f"/api/v1/agents/{attacker['agent_id']}/reports").json()) == 1


@pytest.mark.parametrize(
    "args", [{"title": "", "body": "x"}, {"title": "t" * 201, "body": "x"}, {"title": "t", "body": "  "}]
)
def test_report_save_validates(args):
    agent = _create()
    out = _call(_token(agent["agent_id"]), "report.save", args).json()["result"]
    assert out["status"] == "error"
    assert client.get(f"/api/v1/agents/{agent['agent_id']}/reports").json() == []


@pytest.mark.parametrize("repo_factory", [MemoryReportRepository, lambda: DynamoReportRepository(table("reports"))])
def test_report_repositories_newest_first(repo_factory):
    repo = repo_factory()
    aid = f"agent-{uuid.uuid4().hex[:8]}"
    first = repo.insert(ReportRecord(agent_id=aid, title="one", body="a", created_at=1000.0))
    second = repo.insert(ReportRecord(agent_id=aid, title="two", body="b", trace_id="run_1", created_at=2000.0))
    repo.insert(ReportRecord(agent_id="other-agent", title="x", body="y"))
    listed = repo.list(aid)
    assert [r.report_id for r in listed] == [second.report_id, first.report_id]
    assert listed[0].trace_id == "run_1" and listed[1].trace_id is None
    assert listed[0].created_at == 2000.0
    assert len(repo.list(aid, limit=1)) == 1


def test_status_reports_report_store():
    assert client.get("/api/v1/status").json()["report_store"] in {"memory", "dynamodb"}


# --- runner -----------------------------------------------------------------------


def test_runner_offers_market_research_tools():
    from types import SimpleNamespace

    from identity_gateway.runner import AgentRunner

    calls = []

    def create(**params):
        calls.append(params)
        if len(calls) == 1:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="tool_use", id="t1", name="usaspending_search",
                                    input={"keywords": "cameras", "fiscal_year": 0}),
                    SimpleNamespace(type="tool_use", id="t2", name="report_save",
                                    input={"title": "Cameras", "body": "Report"}),
                ],
                stop_reason="tool_use",
                usage=None,
            )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn", usage=None)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    previous = gateway.runner
    gateway.configure_runner(
        AgentRunner(
            client_factory=lambda: fake,
            call_tool=gateway.gateway_tool_call,
            issue_token=gateway._issue_run_token,
        )
    )
    try:
        agent = _create()
        r = client.post(f"/api/v1/agents/{agent['agent_id']}/runs", json={"task": "Research body cameras"})
        run = client.get(f"/api/v1/agents/{agent['agent_id']}/runs/{r.json()['run_id']}").json()
    finally:
        gateway.configure_runner(previous)

    offered = sorted(t["name"] for t in calls[0]["tools"])
    assert offered == ["report_save", "sam_entity_lookup", "usaspending_search", "web_scrape"]
    assert run["status"] == "completed"
    assert [s["decision"] for s in run["steps"]] == ["allow", "allow"]
    [report] = client.get(f"/api/v1/agents/{agent['agent_id']}/reports").json()
    assert report["run_id"] == run["run_id"] == report["trace_id"]
