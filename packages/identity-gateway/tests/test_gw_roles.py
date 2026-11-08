"""Office roles (docs/ROLES.md), checked with the real Cedar engine (cedarpy) on the committed
policy files, the same files setup.sh puts into Verified Permissions. Offline: no AWS."""

from __future__ import annotations

import itertools
from pathlib import Path

import cedarpy
import pytest

from identity_gateway.authz import AuthzDecision, AuthzRequest, StarterPolicyAuthorizer
from identity_gateway.autonomy import CATEGORIES
from mandate_contracts import TOOLS, cedar
from mandate_contracts.ddb import AutonomyItem, StafferItem
from mandate_contracts.roles import ROLE_TOOLS

POLICY_DIR = Path(__file__).resolve().parents[2] / "mandate-contracts" / "policies"
POLICY_FILES = sorted(POLICY_DIR.glob("*.cedar"))
# cedarpy names policies policy0, policy1, ... in the order they appear; map them back to file names.
POLICY_TEXT = "\n".join(p.read_text() for p in POLICY_FILES)
POLICY_NAMES = {f"policy{i}": p.stem for i, p in enumerate(POLICY_FILES)}


def _plain(typed: dict) -> dict:
    """AVP typed attributes ({"string": "x"}) -> Cedar JSON values ("x")."""
    return {k: next(iter(v.values())) for k, v in typed.items()}


def _uid(identifier: dict) -> dict:
    return {"__entity": {"type": identifier["entityType"], "id": identifier["entityId"]}}


class CedarAuthorizer:
    """The real Cedar engine on the policy files, fed by the same contract helpers AvpAuthorizer uses."""

    def is_authorized(self, req: AuthzRequest, agent: dict | None = None) -> AuthzDecision:
        agent = agent or cedar.agent_entity(req.agent_id, req.office, req.mode, req.role)
        entities = [
            {"uid": _uid(e["identifier"]), "attrs": _plain(e["attributes"]), "parents": []}
            for e in (agent, req.resource)
        ]
        ident = req.resource["identifier"]
        request = {
            "principal": f'Mandate::Agent::"{req.agent_id}"',
            "action": f'Mandate::Action::"{req.tool}"',
            "resource": f'{ident["entityType"]}::"{ident["entityId"]}"',
            "context": _plain(req.context["contextMap"]),
        }
        result = cedarpy.is_authorized(request, POLICY_TEXT, entities)
        assert not result.diagnostics.errors, result.diagnostics.errors
        ids = sorted(POLICY_NAMES[p] for p in result.diagnostics.reasons)
        return AuthzDecision(allowed=result.decision == cedarpy.Decision.Allow, policy_ids=ids)


CEDAR = CedarAuthorizer()
ROLES = list(ROLE_TOOLS)
TOOL_NAMES = list(TOOLS)


def _req(tool, role, *, case_office="rep-x", delegator=True, known=True, account="official"):
    resource = cedar.case_entity("case-1", case_office) if tool == "case_read" else cedar.tool_entity(tool)
    return AuthzRequest(
        agent_id="agent-1",
        office="rep-x",
        mode="official",
        role=role,
        tool=tool,
        resource=resource,
        context=cedar.build_context(
            delegatorActive=delegator,
            recipientKnown=tool == "email_send" and known,
            account=account if tool in ("social_post", "social_reply") else "",
        ),
    )


# ---- each role: one thing it may do, one thing it may not ------------------

ROLE_CASES = [
    # role, allowed tool, denied tool, policy that denies it
    ("chief_of_staff", "social_post", "records_bulk_export", "forbid-bulk-export"),
    ("caseworker", "case_read", "social_post", "role-social-post"),
    ("communications_director", "social_post", "case_read", "role-case-read"),
    ("legislative_aide", "datagov_search", "email_send", "role-email-send"),
    ("intern", "web_scrape", "inbox_read", "role-inbox-read"),
    ("chief_of_staff", "email_send_list", "records_bulk_export", "forbid-bulk-export"),
    ("caseworker", "report_save", "email_send_list", "role-email-send-list"),
    ("communications_director", "social_reply", "usaspending_search", "role-usaspending-search"),
    ("legislative_aide", "sam_entity_lookup", "social_reply", "role-social-reply"),
    ("intern", "census_query", "report_save", "role-report-save"),
]


@pytest.mark.parametrize("role,allowed,denied,policy", ROLE_CASES)
def test_each_role_allows_its_work_and_denies_the_rest_in_real_cedar(role, allowed, denied, policy):
    assert CEDAR.is_authorized(_req(allowed, role)).allowed is True
    no = CEDAR.is_authorized(_req(denied, role))
    assert no.allowed is False and policy in no.policy_ids


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_real_cedar_allows_exactly_the_roles_tool_list(role, tool):
    assert CEDAR.is_authorized(_req(tool, role)).allowed is (tool in ROLE_TOOLS[role])


def test_the_intern_is_read_only():
    allowed = [t for t in TOOL_NAMES if CEDAR.is_authorized(_req(t, "intern")).allowed]
    assert sorted(allowed) == sorted(
        ["datagov_search", "web_scrape", "census_query", "nasa_search", "nps_parks", "noaa_climate"]
    )


# ---- fail closed ---------------------------------------------------------------


@pytest.mark.parametrize("role", ["", "senator", "admin", "staffer", "Chief_Of_Staff"])
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_an_unknown_or_empty_role_is_denied_everything(role, tool):
    assert CEDAR.is_authorized(_req(tool, role)).allowed is False


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_an_agent_entity_with_no_role_attribute_is_denied_everything(tool):
    """An old gateway that never sends `role`: the forbids' `principal has role` guard denies, with no error."""
    req = _req(tool, "chief_of_staff")
    agent = cedar.agent_entity("agent-1", "rep-x", "official", "chief_of_staff")
    del agent["attributes"]["role"]
    assert CEDAR.is_authorized(req, agent).allowed is False


# ---- the starter forbids still win over every role ---------------------------


def test_forbids_still_win_for_the_chief_of_staff():
    bulk = CEDAR.is_authorized(_req("records_bulk_export", "chief_of_staff"))
    assert bulk.allowed is False and bulk.policy_ids == ["forbid-bulk-export"]
    campaign = CEDAR.is_authorized(_req("social_post", "chief_of_staff", account="campaign"))
    assert campaign.allowed is False and campaign.policy_ids == ["forbid-campaign-post"]


def test_a_campaign_reply_is_forbidden_for_every_role_that_may_reply():
    for role in ("chief_of_staff", "communications_director"):
        official = CEDAR.is_authorized(_req("social_reply", role, account="official"))
        assert official.allowed is True and "social-reply-official" in official.policy_ids
        campaign = CEDAR.is_authorized(_req("social_reply", role, account="campaign"))
        assert campaign.allowed is False and campaign.policy_ids == ["forbid-campaign-reply"]


def test_only_the_chief_may_email_the_mailing_list_and_a_staff_account_is_not_needed():
    allowed = [r for r in ROLES if CEDAR.is_authorized(_req("email_send_list", r)).allowed]
    assert allowed == ["chief_of_staff"]
    assert CEDAR.is_authorized(_req("email_send_list", "chief_of_staff", delegator=False)).allowed is False


def test_the_new_permits_need_an_active_delegator():
    for tool in ("census_query", "usaspending_search", "report_save"):
        assert CEDAR.is_authorized(_req(tool, "chief_of_staff")).allowed is True
        assert CEDAR.is_authorized(_req(tool, "chief_of_staff", delegator=False)).allowed is False


def test_the_starter_conditions_still_apply_inside_a_role():
    assert CEDAR.is_authorized(_req("case_read", "caseworker", case_office="rep-y")).allowed is False
    assert CEDAR.is_authorized(_req("email_send", "caseworker", known=False)).allowed is False
    assert CEDAR.is_authorized(_req("inbox_read", "chief_of_staff", delegator=False)).allowed is False


# ---- the Python test double agrees with real Cedar everywhere ------------------


@pytest.mark.parametrize("role", [*ROLES, "", "senator"])
def test_starter_policy_authorizer_matches_real_cedar(role):
    fake = StarterPolicyAuthorizer()
    for tool, case_office, delegator, known, account in itertools.product(
        TOOL_NAMES, ("rep-x", "rep-y"), (True, False), (True, False), ("official", "campaign")
    ):
        req = _req(tool, role, case_office=case_office, delegator=delegator, known=known, account=account)
        real, double = CEDAR.is_authorized(req), fake.is_authorized(req)
        assert double.allowed == real.allowed, (tool, req.context)
        if not real.allowed:  # forbids decide a deny; both must name the same ones
            assert sorted(double.policy_ids) == real.policy_ids, (tool, req.context)


# ---- the gateway reads the role from DynamoDB, never from the client ------------


@pytest.fixture
def cedar_gateway(store, secret, scan_spy):
    from identity_gateway.gateway import Gateway

    return Gateway(store, CEDAR, secret, scan=scan_spy)


def test_the_role_comes_from_dynamodb_not_from_tool_input(cedar_gateway, make_token, handler_spy):
    # maya is a Caseworker in DynamoDB; the agent claims a better role in the tool input.
    out = cedar_gateway.guarded(
        "social_post", {"account": "official", "text": "hi", "role": "chief_of_staff"}, make_token()
    )
    assert out.allowed is False and out.event.policy_ids == ["role-social-post"]
    assert handler_spy.calls == []


def test_a_role_change_in_dynamodb_applies_on_the_next_call(cedar_gateway, store, make_token):
    token = make_token()
    assert cedar_gateway.guarded("inbox_read", {"email_id": "email-1"}, token).allowed is True
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    out = cedar_gateway.guarded("inbox_read", {"email_id": "email-1"}, token)  # same token, still valid
    assert out.allowed is False and out.event.policy_ids == ["role-inbox-read"]


def test_a_staffer_with_no_role_on_file_is_denied(cedar_gateway, store, make_token):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer"))
    out = cedar_gateway.guarded("datagov_search", {"query": "potholes"}, make_token())
    assert out.allowed is False and out.event.policy_ids == ["role-datagov-search"]
    assert "no office role is on file" in out.deny_text()


def test_a_role_deny_tells_the_staffer_why_in_plain_words(cedar_gateway, store, make_token):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    text = cedar_gateway.guarded("email_send", {"to": "a@b.gov", "subject": "s", "body": "b"}, make_token()).deny_text()
    assert "policy: role-email-send" in text
    assert "an Intern's assistant may not send email for the office" in text


def test_a_draft_approved_after_a_role_change_is_checked_against_the_new_role(cedar_gateway, store, make_token, world):
    held = cedar_gateway.guarded("email_send", {"to": world.known_email, "subject": "s", "body": "b"}, make_token())
    assert held.result["status"] == "held_for_review"
    draft_id = held.result["draftId"]
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="legislative_aide"))
    out = cedar_gateway.send_draft(draft_id, store.get_draft(draft_id), {})
    assert out.allowed is False and out.event.policy_ids == ["role-email-send"]


# ---- Backend/policy/tests.sh expects what real Cedar decides ---------------------


def _policy_cases():
    import importlib.util

    path = Path(__file__).resolve().parents[3] / "Backend" / "policy" / "request.py"
    spec = importlib.util.spec_from_file_location("policy_request", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY_CASES = _policy_cases()


@pytest.mark.parametrize("case", list(POLICY_CASES.CASES))
def test_policy_script_cases_match_real_cedar(case):
    body = POLICY_CASES.request(case, "ps-test")  # the exact AWS CLI input tests.sh sends
    agent, resource = body["entities"]["entityList"]
    req = AuthzRequest(
        agent_id=agent["identifier"]["entityId"],
        office=agent["attributes"]["office"]["string"],
        mode=agent["attributes"]["mode"]["string"],
        role=agent["attributes"]["role"]["string"],
        tool=body["action"]["actionId"],
        resource=resource,
        context=body["context"],
    )
    expected = POLICY_CASES.CASES[case][-1]
    assert ("ALLOW" if CEDAR.is_authorized(req).allowed else "DENY") == expected


def test_policy_script_has_an_allow_and_a_deny_for_every_role():
    by_role = {}
    for role, *_, expected in POLICY_CASES.CASES.values():
        by_role.setdefault(role, set()).add(expected)
    for role in ROLES:
        assert by_role[role] == {"ALLOW", "DENY"}, role


# ---- least privilege: the agent never gets more than its staffer's role ----------
# Table-driven from ROLE_TOOLS, TOOLS and the autonomy categories, so a new role, tool or
# category is covered without editing this test.

CALLS = [  # (tool, args) for every tool, with the inputs that pass the starter conditions
    ("inbox_read", {"email_id": "email-1"}),
    ("case_read", {"case_id": "case-1"}),
    ("datagov_search", {"query": "potholes"}),
    ("web_scrape", {"url": "https://example.gov"}),
    ("email_send", {"to": "a@b.gov", "subject": "s", "body": "b"}),
    ("social_post", {"account": "official", "text": "Town hall"}),
    ("records_bulk_export", {"query": "all"}),
    ("email_send_list", {"subject": "News", "body": "Hello"}),
    ("social_reply", {"account": "official", "reply_to": "https://x.com/a/status/1", "text": "Thanks"}),
    ("usaspending_search", {"keywords": "cameras", "fiscal_year": 2025}),
    ("sam_entity_lookup", {"name": "Axon"}),
    ("report_save", {"title": "t", "body": "b"}),
    ("census_query", {"year": "2023", "dataset": "acs/acs1", "get": "NAME", "for": "place:1", "in": ""}),
    ("openfda_search", {"endpoint": "drug/event", "search": "x", "limit": "1"}),
    ("eia_query", {"route": "petroleum/crd/crpdn", "frequency": "monthly", "length": "1", "facet": ""}),
    ("nasa_search", {"q": "mars"}),
    ("noaa_climate", {"datasetid": "GHCND", "locationid": "ZIP:95050", "startdate": "2025-01-01", "enddate": "2025-01-02", "datatypeid": ""}),
    ("nps_parks", {"q": "yosemite", "parkCode": ""}),
    ("bls_series", {"series_id": "CUUR0000SA0", "startyear": "2024", "endyear": "2025"}),
    ("fec_search", {"q": "Smith"}),
    ("epa_search", {"q": "SANTA CLARA"}),
    ("fcc_search", {"q": "KQED"}),
]
OUT_OF_BOUNDS = [  # inputs every role is refused, whatever it may otherwise do
    ("case_read", {"case_id": "case-2"}),  # another office's case
    ("email_send", {"to": "stranger@example.com", "subject": "s", "body": "b"}),  # unknown recipient
    ("social_post", {"account": "campaign", "text": "Vote"}),  # campaign account
    ("social_reply", {"account": "campaign", "reply_to": "1", "text": "Vote"}),  # campaign account
    ("records_bulk_export", {"query": "all"}),
]


def test_calls_cover_every_tool():
    assert sorted(t for t, _ in CALLS) == sorted(TOOLS)


def _grant_everything(store, sid, staffer_id="maya"):
    """The most the staffer could ever hand the agent: every category set to "auto" and granted on `sid`."""
    for category in CATEGORIES:
        store.put_autonomy(staffer_id, category, AutonomyItem(level="auto"))
        store.put_grant(sid, category, staffer_id, "2026-11-08T00:00:00Z", 2**31)
    assert sorted(store.grants_for(sid)) == sorted(CATEGORIES)


@pytest.mark.parametrize("granted", [False, True], ids=["no-grants", "every-grant"])
@pytest.mark.parametrize("role", [*ROLES, None])
def test_the_agent_never_gets_a_tool_its_staffers_role_lacks(
    cedar_gateway, store, make_token, world, handler_spy, role, granted, trusted_replies
):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role=role))
    if granted:
        _grant_everything(store, world.sid)
    token = make_token()
    for tool, args in CALLS:
        out = cedar_gateway.guarded(tool, args, token)
        may = tool in ROLE_TOOLS.get(role or "", ())
        # ALLOW covers both "ran" and "held for review": a held draft is still something the agent did.
        assert out.allowed is may, (role, tool, out.event.reason, out.event.policy_ids)
        if not may:
            assert out.result is None
    for tool, args in OUT_OF_BOUNDS:
        assert cedar_gateway.guarded(tool, args, token).allowed is False, (role, tool, args)
    ran = {name.replace(".", "_") for name in handler_spy.calls}  # handlers use dotted names
    assert ran <= set(ROLE_TOOLS.get(role or "", ())), ran


def test_a_grant_or_auto_outside_the_role_is_ignored_by_the_gateway(cedar_gateway, store, make_token, world, handler_spy):
    # maya is a Caseworker: no posting. Even with posts set to "auto" and granted, Cedar denies first.
    _grant_everything(store, world.sid)
    out = cedar_gateway.guarded("social_post", {"account": "official", "text": "hi"}, make_token())
    assert out.allowed is False and out.event.policy_ids == ["role-social-post"]
    assert handler_spy.calls == [] and store.get_autonomy("maya", "social_post").level == "auto"


def test_forbids_win_over_every_grant_for_the_chief_of_staff(cedar_gateway, store, make_token, world, handler_spy):
    _grant_everything(store, world.chief_sid, "chiefofstaff")  # granted on the chief's own session
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    official = cedar_gateway.guarded("social_post", {"account": "official", "text": "hi"}, token)
    assert official.event.reason == "allowed"  # the grants are live: an official post runs, not held
    handler_spy.calls.clear()
    campaign = cedar_gateway.guarded("social_post", {"account": "campaign", "text": "Vote"}, token)
    bulk = cedar_gateway.guarded("records_bulk_export", {"query": "all"}, token)
    assert campaign.event.policy_ids == ["forbid-campaign-post"] and bulk.event.policy_ids == ["forbid-bulk-export"]
    assert not campaign.allowed and not bulk.allowed and handler_spy.calls == []


# ---- the agent's identity cannot be swapped -------------------------------------


def test_a_mandate_for_one_staffer_cannot_use_another_staffers_session(cedar_gateway, make_token, world, handler_spy):
    # maya (Caseworker) signs a token naming the Chief of Staff's session, to borrow the chief's posting role.
    token = make_token(act="maya", sid=world.chief_sid)
    out = cedar_gateway.guarded("social_post", {"account": "official", "text": "hi"}, token)
    assert out.allowed is False and out.event.reason == "session revoked / delegator inactive"
    assert handler_spy.calls == []


def test_a_mandate_claiming_another_staffer_on_your_session_is_denied(cedar_gateway, make_token, world):
    token = make_token(act="chiefofstaff", sid=world.sid)  # maya's session, the chief's name
    out = cedar_gateway.guarded("social_post", {"account": "official", "text": "hi"}, token)
    assert out.allowed is False and out.event.reason == "session revoked / delegator inactive"


def test_a_replayed_mandate_after_offboarding_is_denied(cedar_gateway, store, make_token):
    token = make_token()
    assert cedar_gateway.guarded("datagov_search", {"query": "x"}, token).allowed is True
    store.set_staffer_active("maya", False)
    store.revoke_sessions_for("maya")
    out = cedar_gateway.guarded("datagov_search", {"query": "x"}, token)  # same, still unexpired token
    assert out.allowed is False and out.event.reason == "session revoked / delegator inactive"


def test_role_and_office_in_tool_input_are_ignored(cedar_gateway, make_token, world):
    args = {"case_id": "case-2", "office": "rep-y", "role": "chief_of_staff", "officeRole": "chief_of_staff"}
    out = cedar_gateway.guarded("case_read", args, make_token())  # case-2 is in rep-y; maya is rep-x
    assert out.allowed is False and out.result is None
