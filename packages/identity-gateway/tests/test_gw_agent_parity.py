"""C2: the assistant can do every Agents-tab job through guarded(), on both policy engines.

One normal path per agent kind: email, mailing-list email, social post and reply, the ten public
research APIs, and procurement market research with a saved report. Each call must leave exactly
one DecisionEvent with the right decision, reason, policy and ATLAS ids. Offline: moto, the Cedar
engines, and stubbed HTTP.
"""

from __future__ import annotations

import json

import pytest
from mandate_contracts import GuardrailFlag
from mandate_contracts.ddb import AutonomyItem, CaseItem, StafferItem
from mcp_tools import agency_apis, handlers
from mcp_tools.registry import get_tool

HELD = "held for staff review"
ALLOWED = "allowed"
APPROVED = "approved by staff"

RESEARCH_CALLS = {
    "census_query": {"year": "2023", "dataset": "acs/acs1", "get": "NAME,B01003_001E", "for": "place:69084", "in": "state:06"},
    "openfda_search": {"endpoint": "drug/event", "search": "patient.drug.medicinalproduct:aspirin", "limit": "5"},
    "eia_query": {"route": "petroleum/crd/crpdn", "frequency": "monthly", "length": "5", "facet": ""},
    "nasa_search": {"q": "mars rover"},
    "noaa_climate": {"datasetid": "GHCND", "locationid": "ZIP:95050", "startdate": "2025-01-01", "enddate": "2025-01-31", "datatypeid": ""},
    "nps_parks": {"q": "yosemite", "parkCode": "yose"},
    "bls_series": {"series_id": "CUUR0000SA0", "startyear": "2024", "endyear": "2025"},
    "fec_search": {"q": "Smith"},
    "epa_search": {"q": "SANTA CLARA"},
    "fcc_search": {"q": "KQED"},
}
API_KEY_ENV = (
    "CENSUS_API_KEY OPENFDA_API_KEY EIA_API_KEY NASA_API_KEY NOAA_CDO_TOKEN NPS_API_KEY "
    "BLS_API_KEY FEC_API_KEY EPA_API_KEY FCC_API_KEY"
).split()


@pytest.fixture(autouse=True)
def _no_connection_module(monkeypatch):
    """These tests are about the assistant's tools; the Lane I connection gate has its own file."""
    import identity_gateway.gateway as gw

    monkeypatch.setattr(gw, "connections", None, raising=False)


@pytest.fixture
def chief(world):
    return {"act": "chiefofstaff", "sid": world.chief_sid}


@pytest.fixture
def http(monkeypatch):
    """Stub the public HTTP call every research API makes; record each URL."""
    calls: list[str] = []

    def fake(url, **kwargs):
        calls.append(url)
        return {
            "status": "ok",
            "simulated": False,
            "http_status": 200,
            "data": {"note": "stubbed agency payload"},
            "credentials_exposed": False,
        }

    monkeypatch.setattr(agency_apis, "_http_json", fake)
    for name in API_KEY_ENV:
        monkeypatch.setenv(name, "test-key")
    return calls


@pytest.fixture
def sink(monkeypatch):
    saved: list[dict] = []

    def put(record):
        saved.append(record)
        return {"report_id": "rpt_test"}

    handlers.set_report_sink(put)
    yield saved
    handlers.set_report_sink(None)


def _events_added(store, events_of, call):
    before = len(events_of(store))
    outcome = call()
    events = events_of(store)
    assert len(events) == before + 1, "every call writes exactly one DecisionEvent"
    assert events[-1].id == outcome.event.id
    return outcome, events[-1]


# ---- email ----------------------------------------------------------------------------------


def test_email_send_is_held_for_review_by_default(engine_gateway, store, events_of, make_token, handler_spy):
    out, event = _events_added(
        store,
        events_of,
        lambda: engine_gateway.guarded("email_send", {"to": "a@b.gov", "subject": "s", "body": "b"}, make_token()),
    )
    assert event.decision == "ALLOW" and event.reason == HELD and event.tool == "email_send"
    assert out.result["status"] == "held_for_review" and handler_spy.calls == []


def test_email_send_runs_when_the_staffer_allowed_the_category(
    engine_gateway, store, events_of, make_token, world, handler_spy, trusted_replies
):
    args = {"to": world.known_email, "subject": "Re", "body": "Update", "in_reply_to": world.trusted_email_id}
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded("email_send", args, make_token()))
    assert event.decision == "ALLOW" and event.reason == ALLOWED
    assert handler_spy.calls == ["email.send"]


def test_email_send_list_is_always_held_even_when_the_chief_set_everything_to_auto(
    engine_gateway, store, events_of, make_token, world, handler_spy
):
    """The mailing_list category is LOCKED: auto and a session grant must not let it run alone."""
    store.put_autonomy("chiefofstaff", "mailing_list", AutonomyItem(level="auto"))
    store.put_grant(world.chief_sid, "mailing_list", "chiefofstaff", "2026-11-08T00:00:00Z", 2**31)
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    out, event = _events_added(
        store, events_of, lambda: engine_gateway.guarded("email_send_list", {"subject": "News", "body": "Hello"}, token)
    )
    assert event.decision == "ALLOW" and event.reason == HELD and event.tool == "email_send_list"
    assert "email-send-list-office" in event.policy_ids
    assert out.result["status"] == "held_for_review" and out.result["category"] == "mailing_list"
    assert handler_spy.calls == []


def test_email_send_list_recipients_come_from_dynamodb_at_send_time_never_from_input(
    engine_gateway, store, make_token, world, monkeypatch
):
    store.put_case("case-3", CaseItem(office="rep-x", constituent="Al", constituent_email="Alex@Example.org", topic="t", status="open"))
    seen: list[dict] = []
    monkeypatch.setitem(handlers.HANDLERS, "email.send_list", lambda args: seen.append(args) or {"status": "simulated", "sent": 0})
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    held = engine_gateway.guarded(
        "email_send_list",
        {"subject": "News", "body": "Hello", "recipients": ["evil@evil.com"], "to": "evil@evil.com"},
        token,
    )
    draft_id = held.result["draftId"]
    draft = store.get_draft(draft_id)
    assert draft.tool == "email_send_list" and set(draft.args) == {"subject", "body"}
    # A constituent added after the hold is still on the list at send time (it is read again).
    store.put_case("case-4", CaseItem(office="rep-x", constituent="Bo", constituent_email="bo@example.org", topic="t", status="open"))
    out = engine_gateway.send_draft(draft_id, draft, {})
    assert out.allowed and out.event.reason == APPROVED
    assert seen[0]["recipients"] == ["alex@example.org", "bo@example.org", "jane.doe@example.com"]  # sorted; rep-y's bob is not on it
    assert "evil@evil.com" not in json.dumps(seen[0])


def test_a_caseworker_cannot_email_the_mailing_list(engine_gateway, store, events_of, make_token, handler_spy):
    out, event = _events_added(
        store, events_of, lambda: engine_gateway.guarded("email_send_list", {"subject": "s", "body": "b"}, make_token())
    )
    assert event.decision == "DENY" and event.reason == "denied by policy" and out.result is None
    assert event.policy_ids == ["role-email-send-list"]
    assert set(get_tool("email.send_list").atlas_techniques) <= set(event.atlas)
    assert handler_spy.calls == []


# ---- social ----------------------------------------------------------------------------------


def test_social_post_with_a_platform_is_held_for_review(engine_gateway, store, events_of, make_token, maya_posts, handler_spy):
    args = {"account": "official", "text": "Town hall Tuesday", "platform": "x"}
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded("social_post", args, make_token()))
    assert event.decision == "ALLOW" and event.reason == HELD
    assert "social-post-official" in event.policy_ids
    assert out.result["category"] == "social_post" and store.get_draft(out.result["draftId"]).args["platform"] == "x"
    assert handler_spy.calls == []


def test_social_reply_on_the_official_account_is_held_for_review(engine_gateway, store, events_of, make_token, maya_posts, handler_spy):
    args = {"account": "official", "reply_to": "https://x.com/someone/status/12345", "text": "Thanks!", "platform": "x"}
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded("social_reply", args, make_token()))
    assert event.decision == "ALLOW" and event.reason == HELD and event.tool == "social_reply"
    assert "social-reply-official" in event.policy_ids
    assert out.result["status"] == "held_for_review" and out.result["category"] == "social_reply"
    assert handler_spy.calls == []


def test_an_approved_social_reply_runs_the_reply_handler(engine_gateway, store, make_token, maya_posts, handler_spy):
    args = {"account": "official", "reply_to": "https://x.com/someone/status/12345", "text": "Thanks!", "platform": "x"}
    held = engine_gateway.guarded("social_reply", args, make_token())
    draft_id = held.result["draftId"]
    out = engine_gateway.send_draft(draft_id, store.get_draft(draft_id), {})
    assert out.allowed and out.event.reason == APPROVED and handler_spy.calls == ["social.reply"]


def test_a_campaign_account_reply_is_denied_by_the_forbid_even_for_the_chief(
    engine_gateway, store, events_of, make_token, world, handler_spy
):
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    args = {"account": "campaign", "reply_to": "1", "text": "Vote!", "platform": "x"}
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded("social_reply", args, token))
    assert event.decision == "DENY" and event.policy_ids == ["forbid-campaign-reply"] and out.result is None
    assert set(get_tool("social.reply").atlas_techniques) <= set(event.atlas)  # registry fallback
    assert handler_spy.calls == []


# ---- research --------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", list(RESEARCH_CALLS))
def test_each_research_api_runs_through_its_handler_and_its_output_is_scanned(
    engine_gateway, store, events_of, make_token, handler_spy, scan_spy, http, tool
):
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded(tool, RESEARCH_CALLS[tool], make_token()))
    assert event.decision == "ALLOW" and event.reason == ALLOWED and event.tool == tool
    assert "research-tools" in event.policy_ids
    assert handler_spy.calls == [tool.replace("_", ".", 1)] and len(http) == 1
    assert out.result is not None and scan_spy.calls == [json.dumps(out.result)]


def test_research_input_reaches_the_handler_under_its_api_names(engine_gateway, make_token, http):
    """`for`/`in`/`parkCode` are aliases in the contract; the handler reads them by those names."""
    engine_gateway.guarded("census_query", RESEARCH_CALLS["census_query"], make_token())
    engine_gateway.guarded("nps_parks", RESEARCH_CALLS["nps_parks"], make_token())
    assert "69084" in http[0] and "state" in http[0]
    assert "yose" in http[1]


def test_a_flagged_research_result_is_labeled_but_passed_on_unchanged(
    engine_gateway, make_token, http, scan_spy, store, events_of
):
    scan_spy.flag = GuardrailFlag(flagged=True, categories=["PROMPT_ATTACK"])
    out = engine_gateway.guarded("census_query", RESEARCH_CALLS["census_query"], make_token())
    assert out.allowed and out.result is not None and out.event.guardrail.flagged is True
    assert out.event.atlas != []


# ---- market research -------------------------------------------------------------------------


@pytest.fixture
def aide(store):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="legislative_aide"))


@pytest.mark.parametrize(
    "tool,args",
    [
        ("usaspending_search", {"keywords": "body worn cameras", "fiscal_year": 2025}),
        ("sam_entity_lookup", {"name": "Axon Enterprise"}),
    ],
)
def test_procurement_lookups_run_and_their_output_is_scanned(
    engine_gateway, store, events_of, make_token, aide, handler_spy, scan_spy, tool, args
):
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded(tool, args, make_token()))
    assert event.decision == "ALLOW" and event.reason == ALLOWED and "research-tools" in event.policy_ids
    assert handler_spy.calls == [tool.replace("_", ".", 1)]
    assert scan_spy.calls == [json.dumps(out.result)]


def test_report_save_stores_under_the_gateway_identity_never_the_one_in_input(
    engine_gateway, store, events_of, make_token, aide, sink, world
):
    args = {"title": "Camera vendors", "body": "# Findings\n- Axon", "agent_id": "someone-else", "trace_id": "forged", "run_id": "run-9"}
    out, event = _events_added(store, events_of, lambda: engine_gateway.guarded("report_save", args, make_token()))
    assert event.decision == "ALLOW" and event.reason == ALLOWED and "report-save" in event.policy_ids
    assert out.result["status"] == "saved"
    assert sink == [{"agent_id": "assistant-maya", "title": "Camera vendors", "body": "# Findings\n- Axon", "trace_id": world.sid, "run_id": None}]


def test_an_intern_cannot_reach_procurement_or_save_reports(engine_gateway, store, make_token, handler_spy, sink):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    for tool, args in (
        ("usaspending_search", {"keywords": "cameras", "fiscal_year": 2025}),
        ("sam_entity_lookup", {"name": "Axon"}),
        ("report_save", {"title": "t", "body": "b"}),
    ):
        out = engine_gateway.guarded(tool, args, make_token())
        assert not out.allowed and out.event.policy_ids == [f"role-{tool.replace('_', '-')}"]
    assert handler_spy.calls == [] and sink == []
