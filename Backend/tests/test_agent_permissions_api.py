"""GET /api/v1/agent-permissions?kind=... : the permission picker's catalog (docs/loops/agent-permissions.md, C3).

For one kind of agent it lists every registry tool with whether the signed-in staffer may give it:
  options     = the kind's tool ceiling  AND  the caller's office role
  recommended = the kind's least-privilege set, AND the role
The role comes from the STAFFER item in the mandate store (DynamoDB), never from the request or the token.

These tests use the production role resolver on a moto mandate store (the `production_roles` fixture), not the
legacy stand-in in conftest.py, so a missing wiring in Backend/app/agent_iam.py fails them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mandate_contracts.ddb import StafferItem
from mandate_contracts.roles import ROLE_LABELS, ROLE_TOOLS, mandate_tool_name

from app.main import app
from identity_gateway.agent_types import AGENT_TYPES
from mcp_tools.registry import TOOL_REGISTRY
from tests.helpers import auth, login

client = TestClient(app)
pytestmark = [pytest.mark.production_roles, pytest.mark.usefixtures("cognito_login", "production_roles")]

URL = "/api/v1/agent-permissions"
KINDS = ["email", "social", "research", "market_research", "custom"]

# user -> office role (tests/helpers.py PRODUCTION_ROLE_STAFFERS)
USERS = {
    "chiefofstaff": "chief_of_staff",
    "maya": "caseworker",
    "scuacm": "communications_director",
    "a10": "legislative_aide",
    "aws": "intern",
}

# Least privilege per kind (the plan, Design 6). Written out here, not read from the code under test.
RESEARCH_READS = {
    "web.scrape", "datagov.search", "census.query", "openfda.search", "eia.query", "nasa.search",
    "noaa.climate", "nps.parks", "bls.series", "fec.search", "epa.search", "fcc.search",
}
LEAST_PRIVILEGE = {
    "email": {"email.send"},
    "social": {"social.post", "social.reply"},
    "research": RESEARCH_READS,
    "market_research": {"usaspending.search", "sam.entity_lookup", "web.scrape", "report.save"},
    "custom": set(),
}
OUTBOUND = {"email.send", "email.send_list", "social.post", "social.reply"}


def _role_set(role: str) -> set[str]:
    """Registry names a role may use (the contracts' single role table)."""
    return {t for t in TOOL_REGISTRY if mandate_tool_name(t) in ROLE_TOOLS[role]}


def _ceiling(kind: str) -> set[str]:
    allowed = AGENT_TYPES[kind].allowed_tools
    return set(TOOL_REGISTRY) if allowed is None else set(allowed)


def _get(user: str, kind: str, **kwargs):
    return client.get(URL, params={"kind": kind}, headers=login(user), **kwargs)


def _tools(user: str, kind: str) -> dict[str, dict]:
    r = _get(user, kind)
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in r.json()["tools"]}


# ---- options and recommended: kind x role matrix ----------------------------------------------------


@pytest.mark.parametrize("user,role", sorted(USERS.items()))
@pytest.mark.parametrize("kind", KINDS)
def test_options_are_the_kind_ceiling_intersected_with_the_callers_role(user, role, kind):
    """Removing the role term would offer (and so let a staffer pick) a tool their office role forbids;
    removing the kind term would offer an outbound tool to a research agent."""
    r = _get(user, kind)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == kind
    assert body["role"] == role
    assert body["role_label"] == ROLE_LABELS[role]
    tools = {t["name"]: t for t in body["tools"]}
    available = {n for n, t in tools.items() if t["allowed_for_kind"] and t["allowed_for_role"]}
    assert available == _ceiling(kind) & _role_set(role)
    for name, t in tools.items():
        assert t["allowed_for_kind"] is (name in _ceiling(kind)), name
        assert t["allowed_for_role"] is (name in _role_set(role)), name


@pytest.mark.parametrize("user,role", sorted(USERS.items()))
@pytest.mark.parametrize("kind", KINDS)
def test_recommended_is_least_privilege_for_the_kind_and_the_role(user, role, kind):
    """Recommending more than least privilege pre-ticks power the agent does not need;
    recommending outside the role pre-ticks a box the server would then refuse."""
    tools = _tools(user, kind)
    recommended = {n for n, t in tools.items() if t["recommended"]}
    assert recommended == LEAST_PRIVILEGE[kind] & _role_set(role)
    for name in recommended:
        assert tools[name]["allowed_for_kind"] and tools[name]["allowed_for_role"], name


def test_the_recommendation_per_kind_for_the_chief_of_staff_is_exactly_the_plan():
    """Pins the plan's per-kind recommendation as literal sets (a typo in the role table cannot move them)."""
    got = {kind: {n for n, t in _tools("chiefofstaff", kind).items() if t["recommended"]} for kind in KINDS}
    assert got["email"] == {"email.send"}  # NOT email.send_list (that mails every constituent)
    assert got["social"] == {"social.post", "social.reply"}
    assert got["research"] == RESEARCH_READS
    assert got["market_research"] == {"usaspending.search", "sam.entity_lookup", "web.scrape", "report.save"}
    assert got["custom"] == set()


def test_a_caseworker_cannot_pick_the_mailing_list_and_is_told_why():
    """Removing the role cap would let a caseworker's email agent mail the whole constituent list."""
    t = _tools("maya", "email")["email.send_list"]
    assert t["allowed_for_kind"] is True
    assert t["allowed_for_role"] is False
    assert t["recommended"] is False
    assert isinstance(t["reason_unavailable"], str) and t["reason_unavailable"].strip()
    chief = _tools("chiefofstaff", "email")["email.send_list"]
    assert chief["allowed_for_role"] is True and chief["recommended"] is False
    assert not chief["reason_unavailable"]


def test_a_research_agent_never_offers_an_outbound_tool_even_to_the_chief():
    tools = _tools("chiefofstaff", "research")
    for name in OUTBOUND:
        assert tools[name]["allowed_for_kind"] is False
        assert tools[name]["allowed_for_role"] is True  # the role would allow it; the kind does not
        assert tools[name]["reason_unavailable"]


def test_an_intern_gets_only_the_six_intern_tools_on_a_custom_agent():
    tools = _tools("aws", "custom")
    offered = {n for n, t in tools.items() if t["allowed_for_kind"] and t["allowed_for_role"]}
    assert offered == {"datagov.search", "web.scrape", "census.query", "nasa.search", "nps.parks", "noaa.climate"}


# ---- the shape of each tool entry ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_every_registry_tool_is_listed_once_with_the_documented_fields(kind):
    r = _get("maya", kind)
    assert r.status_code == 200, r.text
    names = [t["name"] for t in r.json()["tools"]]
    assert sorted(names) == sorted(TOOL_REGISTRY)
    assert len(names) == len(set(names))
    for t in r.json()["tools"]:
        for key in (
            "name", "label", "effect", "risk", "outbound", "held_for_review",
            "allowed_for_kind", "allowed_for_role", "recommended", "reason_unavailable",
        ):
            assert key in t, (t["name"], key)


def test_each_tool_has_a_plain_words_effect_a_risk_and_an_outbound_flag():
    """The side panel shows `effect` to a non-technical staffer: no policy ids, no tool-code names."""
    risks = {t.risk_level for t in TOOL_REGISTRY.values()}
    for name, t in _tools("chiefofstaff", "custom").items():
        assert isinstance(t["label"], str) and t["label"].strip(), name
        assert isinstance(t["effect"], str) and len(t["effect"].strip()) >= 10, name
        lowered = t["effect"].lower()
        for jargon in ("::", "mandate", "cedar", "permit(", "forbid", "role-", "principal", mandate_tool_name(name)):
            assert jargon not in lowered, (name, jargon, t["effect"])
        assert t["risk"] in risks, (name, t["risk"])
        assert t["outbound"] is (name in OUTBOUND), name
        assert t["held_for_review"] is (name in OUTBOUND), name


def test_unavailable_tools_say_why_and_available_ones_do_not():
    for user in USERS:
        for kind in KINDS:
            for name, t in _tools(user, kind).items():
                available = t["allowed_for_kind"] and t["allowed_for_role"]
                if available:
                    assert not t["reason_unavailable"], (user, kind, name)
                else:
                    assert isinstance(t["reason_unavailable"], str) and t["reason_unavailable"].strip(), (
                        user, kind, name,
                    )
                    assert "::" not in t["reason_unavailable"]
                    assert t["recommended"] is False


# ---- the role is the STAFFER item, nothing the caller sends ---------------------------------------------


def test_the_role_comes_from_the_staffer_item_not_the_request():
    """Trusting a role/office from the query string, header or token would let anyone pick chief_of_staff."""
    sneaky_query = {"kind": "email", "role": "chief_of_staff", "office_role": "chief_of_staff", "officeRole": "chief_of_staff"}
    r = client.get(
        URL,
        params=sneaky_query,
        headers={**login("maya"), "X-Office-Role": "chief_of_staff", "X-Role": "chief_of_staff"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "caseworker"
    send_list = next(t for t in r.json()["tools"] if t["name"] == "email.send_list")
    assert send_list["allowed_for_role"] is False
    # A role claim inside the signed Cognito token is not the source either.
    forged = auth("maya", groups=("staffer",), officeRole="chief_of_staff", **{"custom:role": "chief_of_staff"})
    r = client.get(URL, params={"kind": "email"}, headers=forged)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "caseworker"


def test_the_admin_cognito_group_does_not_widen_the_office_role():
    """If the admin group bypassed the role, an admin-group caseworker could pick every tool."""
    r = client.get(URL, params={"kind": "custom"}, headers=login("admin-caseworker"))
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "caseworker"
    tools = {t["name"]: t for t in r.json()["tools"]}
    assert tools["email.send_list"]["allowed_for_role"] is False
    assert tools["social.post"]["allowed_for_role"] is False
    assert tools["email.send"]["allowed_for_role"] is True


def test_the_role_is_read_fresh_from_dynamodb_on_every_request(production_roles):
    """A cached role would keep offering tools to someone who was just demoted."""
    assert _tools("scuacm", "social")["social.post"]["allowed_for_role"] is True
    production_roles.put_staffer(
        "scuacm", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern")
    )
    after = _tools("scuacm", "social")
    assert after["social.post"]["allowed_for_role"] is False
    assert not any(t["recommended"] for t in after.values())
    assert _get("scuacm", "social").json()["role"] == "intern"  # the label follows the new role too


@pytest.mark.parametrize("user", ["retired", "norole", "ghost"])
def test_an_offboarded_roleless_or_unknown_staffer_is_offered_nothing(user):
    """Fail closed: inactive, no officeRole and no STAFFER item at all must never yield an available tool
    (either a refusal or an all-false catalog)."""
    r = _get(user, "custom")
    if r.status_code != 200:
        assert r.status_code == 403, r.text
        return
    tools = r.json()["tools"]
    assert tools and not any(t["allowed_for_role"] or t["recommended"] for t in tools)


# ---- request validation and login -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["hacker", "Email", "email ", "", "email.send"])
def test_an_unknown_kind_is_a_422(kind):
    assert _get("chiefofstaff", kind).status_code == 422


def test_a_plain_staffer_can_read_the_catalog_and_an_admin_too():
    assert _get("maya", "email").status_code == 200  # Cognito group "staffer"
    assert _get("chiefofstaff", "email").status_code == 200  # group "admin"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic Zm9vOmJhcg=="}],
    ids=["no-login", "garbage", "basic"],
)
def test_no_valid_login_is_a_401(headers):
    assert client.get(URL, params={"kind": "email"}, headers=headers).status_code == 401


def test_the_catalog_is_read_only():
    for method in ("post", "put", "patch", "delete"):
        r = client.request(method.upper(), URL, params={"kind": "email"}, headers=login("chiefofstaff"), json={})
        assert r.status_code in (404, 405), (method, r.status_code)


# ---- wiring ------------------------------------------------------------------------------------------------


def test_the_agents_api_has_a_role_resolver_hook_and_actors_know_if_they_are_admins():
    """agent_iam wires the resolver to the mandate store; Actor.is_admin comes from the Cognito group."""
    from identity_gateway import agents_api

    assert callable(getattr(agents_api, "set_role_resolver", None))
    assert "is_admin" in agents_api.Actor.__dataclass_fields__
