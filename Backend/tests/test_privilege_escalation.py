"""Privilege-escalation attempts on the Agents tab (docs/loops/agent-permissions.md, C4 Backend part).

Every test is a direct API call, so the UI (which never shows a forbidden option) is bypassed by construction.
Everything goes through the production wiring: signed Cognito test tokens, `Backend/app/agent_iam.py`'s real
actor and role resolver, and a moto mandate store holding the STAFFER items (see the `production_roles`
fixture in conftest.py). Nothing here is a stub of the guard being tested.

Each test's docstring names what REMOVING its guard would let through. Most tests first make a control request
that must succeed (the same caller doing something inside their role) so a 403 can only come from the cap.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mandate_contracts.ddb import StafferItem

from app.main import app
from identity_gateway import agents_api
from identity_gateway.runner import new_run
from mcp_tools.registry import TOOL_REGISTRY
from tests.helpers import login

client = TestClient(app)
pytestmark = [pytest.mark.production_roles, pytest.mark.usefixtures("cognito_login", "production_roles")]

API = "/api/v1"
SCRAPE = {"url": "https://example.gov", "q": ""}
REPLY = {"platform": "x", "reply_to": "12345", "text": "Thanks for writing to our office."}
MAIL = {"to": "constituent@example.gov", "subject": "Hello", "body": "Hi"}
MAIL_LIST = {"subject": "Town hall", "body": "Join us Thursday."}


# ---- helpers ------------------------------------------------------------------------------------------


@pytest.fixture
def audit(monkeypatch):
    """Every audit event appended while the test runs (agent-less denials are not visible through GET /audit)."""
    events: list[dict] = []
    real = agents_api.store.append_audit

    def spy(event):
        recorded = real(event)
        events.append(recorded)
        return recorded

    monkeypatch.setattr(agents_api.store, "append_audit", spy)
    return events


def _was_denied(events, user, tool):
    return any(
        e.get("type") == "grant_denied"
        and e.get("decision") == "deny"
        and e.get("actor") == f"user:{user}"
        and (e.get("tool") == tool or tool in (e.get("tools") or []))
        for e in events
    )


def _body(**over) -> dict:
    return {
        "agent_id": f"agent-esc-{uuid.uuid4().hex[:8]}",
        "name": "Escalation test",
        "agency": "Dept. of Testing",
        "kind": "custom",
        "tool_grants": ["web.scrape"],
        **over,
    }


def create(user, **over):
    return client.post(f"{API}/agents", json=_body(**over), headers=login(user))


def create_ok(user, **over) -> dict:
    r = create(user, **over)
    assert r.status_code == 201, r.text
    return r.json()


def agent_state(user, aid) -> tuple[list[str], int]:
    r = client.get(f"{API}/agents/{aid}", headers=login(user))
    assert r.status_code == 200, r.text
    return r.json()["tool_grants"], r.json()["token_version"]


def put_grants(user, aid, tools, **extra):
    return client.put(f"{API}/agents/{aid}/grants", json={"tools": tools, **extra}, headers=login(user))


def post_grant(user, aid, tool):
    return client.post(f"{API}/agents/{aid}/grants/{quote(tool, safe='')}", headers=login(user))


def mint(user, aid) -> str:
    r = client.post(f"{API}/auth/token", json={"agent_id": aid}, headers=login(user))
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def call(user, token, tool, arguments):
    return client.post(
        f"{API}/agents/tool-call",
        json={"tool": tool, "arguments": arguments},
        headers={**login(user), "X-Agent-Token": token},
    )


def set_role(store, user, office_role, *, group="admin", active=True):
    store.put_staffer(user, StafferItem(active=active, office="rep-x", role=group, office_role=office_role))


def _ownerless(grants, **kw):
    """An agent with no owner (like the seeded demo agents), created straight in the store."""
    return agents_api.store.create_agent(
        "Shared agent", "Dept.", sorted(grants), f"agent-shared-{uuid.uuid4().hex[:8]}",
        kind=kw.pop("kind", "custom"), recipients=["constituent@example.gov"], **kw,
    )


# ---- forged create payloads ----------------------------------------------------------------------------

BEYOND_ROLE = [
    pytest.param("maya", "custom", ["web.scrape", "social.post"], "social.post", id="caseworker-posts"),
    pytest.param("maya", "email", ["email.send", "email.send_list"], "email.send_list", id="caseworker-mails-the-list"),
    pytest.param("aws", "custom", ["web.scrape", "email.send"], "email.send", id="intern-emails"),
    pytest.param("a10", "custom", ["datagov.search", "email.send"], "email.send", id="aide-emails"),
    pytest.param("scuacm", "custom", ["web.scrape", "email.send_list"], "email.send_list", id="comms-mails-the-list"),
    pytest.param("maya", "custom", ["web.scrape", "usaspending.search"], "usaspending.search", id="caseworker-procurement"),
]


@pytest.mark.parametrize("user,kind,tools,extra", BEYOND_ROLE)
def test_a_forged_create_with_a_tool_beyond_the_role_is_403_and_audited(audit, user, kind, tools, extra):
    """Without the role cap on create, any staffer could POST /agents with a tool their office role forbids
    (a caseworker with social.post, an intern with email.send) and run it with the agent's own authority."""
    within_role = [t for t in tools if t != extra][:1] if kind != "email" else ["email.send"]
    create_ok(user, kind=kind, tool_grants=within_role)  # control: the caller can create inside their role
    forged = _body(kind=kind, tool_grants=tools)
    r = client.post(f"{API}/agents", json=forged, headers=login(user))
    assert r.status_code == 403, r.text
    assert agents_api.store.get_agent(forged["agent_id"]) is None  # nothing was created
    assert _was_denied(audit, user, extra), audit


@pytest.mark.parametrize("user", ["maya", "scuacm"])
def test_the_mailing_list_tool_is_chief_only_a_staffer_asking_for_it_gets_403(audit, user):
    """email.send_list mails every constituent. Without the role cap a staffer-group user (or a caseworker who
    edits the request) could mail them all."""
    create_ok("chiefofstaff", kind="email", tool_grants=["email.send_list"])  # control: the chief may
    r = create(user, kind="email", tool_grants=["email.send_list"])
    assert r.status_code == 403, r.text
    assert _was_denied(audit, user, "email.send_list")


def test_the_admin_cognito_group_does_not_bypass_the_office_role(audit):
    """If the admin group bypassed the role cap, an admin-group caseworker or intern could create agents with
    tools their office role forbids. The group opens the pages; the STAFFER item's officeRole decides tools."""
    create_ok("admin-caseworker", kind="email", tool_grants=["email.send"])  # control
    assert create("admin-caseworker", kind="email", tool_grants=["email.send_list"]).status_code == 403
    assert create("admin-caseworker", kind="custom", tool_grants=["social.post"]).status_code == 403
    assert create("admin-intern", kind="custom", tool_grants=["email.send"]).status_code == 403
    mine = create_ok("admin-caseworker", kind="email", tool_grants=["email.send"])
    before = agent_state("admin-caseworker", mine["agent_id"])
    assert post_grant("admin-caseworker", mine["agent_id"], "email.send_list").status_code == 403
    assert put_grants("admin-caseworker", mine["agent_id"], ["email.send", "email.send_list"]).status_code == 403
    assert agent_state("admin-caseworker", mine["agent_id"]) == before


@pytest.mark.parametrize("who", ["retired", "ghost", "norole"])
def test_an_offboarded_unknown_or_roleless_caller_can_create_nothing(who):
    """Without the active/role check a still-valid Cognito token of an offboarded staffer (or one with no
    STAFFER item or no officeRole) could keep creating agents. Fail closed."""
    create_ok("maya", tool_grants=["web.scrape"])  # control
    r = create(who, tool_grants=["web.scrape"])
    assert r.status_code == 403, r.text


# ---- tool-name variants, unknown tools, duplicates ---------------------------------------------------------

VARIANTS = {
    "capitalised": "Email.send",
    "upper-case": "EMAIL.SEND",
    "leading-space": " email.send",
    "trailing-space": "email.send ",
    "trailing-newline": "email.send\n",
    "tab": "email.send\t",
    "one-dot-leader": "email․send",
    "fullwidth": "ｅｍａｉｌ．ｓｅｎｄ",
    "zero-width-space": "email​.send",
    "zero-width-joiner-at-end": "email.send‍",
    "cyrillic-e": "еmail.send",
    "underscore-form": "email_send",
    "double-dot": "email..send",
}
VARIANT_PARAMS = [pytest.param(v, id=k) for k, v in VARIANTS.items()]


@pytest.mark.parametrize("variant", VARIANT_PARAMS)
def test_a_tool_name_variant_on_create_is_400_unknown_not_normalised(variant):
    """Without exact-name matching, a lookalike (case, space, unicode dot, zero-width, mandate underscore name)
    could slip past a check that compares one spelling and then be normalised into a real grant."""
    create_ok("maya", kind="email", tool_grants=["email.send"])  # control: the exact name works
    for grants in ([variant], ["web.scrape", variant]):
        forged = _body(kind="custom", tool_grants=grants)
        r = client.post(f"{API}/agents", json=forged, headers=login("maya"))
        assert r.status_code == 400, (grants, r.status_code, r.text)
        assert "unknown_tools" in r.text
        assert agents_api.store.get_agent(forged["agent_id"]) is None


@pytest.mark.parametrize("variant", VARIANT_PARAMS)
def test_a_tool_name_variant_on_grant_replace_is_400_and_changes_nothing(variant):
    """The same exact-name guard on PUT /agents/{id}/grants: a variant must not replace or extend the grants."""
    aid = create_ok("maya", kind="email", tool_grants=["email.send"])["agent_id"]
    assert put_grants("maya", aid, ["email.send"]).status_code == 200  # control
    before = agent_state("maya", aid)
    for tools in ([variant], ["email.send", variant]):
        r = put_grants("maya", aid, tools)
        assert r.status_code == 400, (tools, r.status_code, r.text)
        assert "unknown_tools" in r.text
        assert agent_state("maya", aid) == before


@pytest.mark.parametrize("variant", VARIANT_PARAMS)
def test_a_tool_name_variant_on_single_grant_is_refused_and_changes_nothing(variant):
    """The same guard on POST /agents/{id}/grants/{tool}. 400 per the plan; the route answered 404 for an
    unknown tool before, so either is accepted. What matters: it is never a 2xx and nothing changes."""
    aid = create_ok("maya", kind="email", tool_grants=["email.send"])["agent_id"]
    assert post_grant("maya", aid, "datagov.search").status_code == 200  # control: exact, in role and kind
    before = agent_state("maya", aid)
    r = post_grant("maya", aid, variant)
    assert r.status_code in (400, 404), (r.status_code, r.text)
    assert agent_state("maya", aid) == before


@pytest.mark.parametrize("name", ["not.a.tool", "case_read", "records_bulk_export", "email", ".", "email.send.extra"])
def test_an_unknown_tool_is_400_on_create_and_on_grant_replace(name):
    """Without the registry check an unknown name would be stored as a grant (and a later registry entry with
    that name would arrive pre-granted)."""
    aid = create_ok("maya", tool_grants=["web.scrape"])["agent_id"]
    forged = _body(tool_grants=[name])
    r = client.post(f"{API}/agents", json=forged, headers=login("maya"))
    assert r.status_code == 400 and "unknown_tools" in r.text, r.text
    assert agents_api.store.get_agent(forged["agent_id"]) is None
    before = agent_state("maya", aid)
    r = put_grants("maya", aid, [name])
    assert r.status_code == 400 and "unknown_tools" in r.text, r.text
    assert agent_state("maya", aid) == before


def test_duplicate_tools_are_400_not_silently_merged():
    """Without the duplicate check, ['email.send','email.send'] (or a padded list) passes validation by
    counting the same tool twice; the plan rejects it rather than de-duplicating it."""
    create_ok("maya", tool_grants=["web.scrape", "datagov.search"])  # control
    forged = _body(tool_grants=["web.scrape", "web.scrape"])
    r = client.post(f"{API}/agents", json=forged, headers=login("maya"))
    assert r.status_code == 400 and "duplicate_tools" in r.text, r.text
    assert agents_api.store.get_agent(forged["agent_id"]) is None

    aid = create_ok("maya", tool_grants=["web.scrape"])["agent_id"]
    before = agent_state("maya", aid)
    r = put_grants("maya", aid, ["web.scrape", "datagov.search", "web.scrape"])
    assert r.status_code == 400 and "duplicate_tools" in r.text, r.text
    assert agent_state("maya", aid) == before


# ---- editing an existing agent to add power -----------------------------------------------------------------

EDIT_BEYOND_ROLE = [
    pytest.param("maya", "email", ["email.send"], "email.send_list", id="caseworker-adds-the-list"),
    pytest.param("maya", "custom", ["web.scrape"], "social.post", id="caseworker-adds-posting"),
    pytest.param("scuacm", "custom", ["web.scrape"], "email.send_list", id="comms-adds-the-list"),
    pytest.param("aws", "custom", ["web.scrape"], "email.send", id="intern-adds-email"),
    pytest.param("a10", "custom", ["web.scrape"], "social.reply", id="aide-adds-reply"),
    pytest.param("admin-caseworker", "email", ["email.send"], "email.send_list", id="admin-group-caseworker"),
]


@pytest.mark.parametrize("user,kind,base,extra", EDIT_BEYOND_ROLE)
def test_adding_a_tool_beyond_the_role_to_an_existing_agent_is_403(audit, user, kind, base, extra):
    """Creating a small agent and then PUT/POST-ing a bigger grant set would sidestep the create-time cap.
    Without the cap on the grant routes the grants (and the workload token's powers) would grow."""
    aid = create_ok(user, kind=kind, tool_grants=base)["agent_id"]
    assert put_grants(user, aid, base).status_code == 200  # control: replacing within role works
    before = agent_state(user, aid)
    r = put_grants(user, aid, [*base, extra])
    assert r.status_code == 403, r.text
    assert agent_state(user, aid) == before  # grants and token_version unchanged
    r = post_grant(user, aid, extra)
    assert r.status_code == 403, r.text
    assert agent_state(user, aid) == before
    assert _was_denied(audit, user, extra)


# ---- request bodies cannot name an owner, office or user ----------------------------------------------------


@pytest.mark.parametrize("field", ["owner_id", "office", "user_id", "ownerId", "created_by", "officeRole"])
def test_a_create_body_naming_an_owner_office_or_user_is_422_and_the_owner_is_the_caller(field):
    """Without extra=forbid the field would be silently dropped (or, worse, honoured): an agent filed under
    another user or office. The owner is always the signed-in caller."""
    mine = create_ok("maya")
    assert mine["owner_id"] == "maya"  # control
    forged = _body(**{field: "chiefofstaff" if field != "office" else "rep-z"})
    r = client.post(f"{API}/agents", json=forged, headers=login("maya"))
    assert r.status_code == 422, r.text
    assert agents_api.store.get_agent(forged["agent_id"]) is None


@pytest.mark.parametrize("field", ["owner_id", "office", "user_id"])
def test_a_grants_body_naming_an_owner_office_or_user_is_422(field):
    """GrantUpdate forbids extras: a grants edit cannot carry a different owner, office or acting user."""
    aid = create_ok("maya", tool_grants=["web.scrape"])["agent_id"]
    assert put_grants("maya", aid, ["web.scrape", "datagov.search"]).status_code == 200  # control
    before = agent_state("maya", aid)
    r = put_grants("maya", aid, ["web.scrape"], **{field: "chiefofstaff"})
    assert r.status_code == 422, r.text
    assert agent_state("maya", aid) == before


@pytest.mark.parametrize("other", ["bronco", "dana", "admin-comms"])
def test_editing_another_users_agent_is_404_even_for_the_same_role_another_office_or_an_admin(other):
    """Without the ownership check (404, not 403, so ids can't be probed) a colleague with the same role, a user
    in another office or an admin-group user could change someone else's agent grants."""
    aid = create_ok("maya", kind="email", tool_grants=["email.send"])["agent_id"]
    assert put_grants("maya", aid, ["email.send", "datagov.search"]).status_code == 200  # control: the owner can
    before = agent_state("maya", aid)
    assert put_grants(other, aid, ["email.send"]).status_code == 404
    assert post_grant(other, aid, "datagov.search").status_code == 404
    assert client.get(f"{API}/agents/{aid}", headers=login(other)).status_code == 404
    assert agent_state("maya", aid) == before


# ---- agents with no owner ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("user", ["maya", "aws", "scuacm"])
def test_a_non_admin_editing_an_ownerless_shared_agent_is_403(user):
    """Ownerless (shared demo) agents are visible to everyone. Without the admin-only rule any staffer could
    rewrite the grants of an agent that other people's runs use."""
    shared = _ownerless({"email.send"}, kind="custom")
    before = agent_state("chiefofstaff", shared.agent_id)
    # control: an admin whose role covers the change may edit it
    assert put_grants("chiefofstaff", shared.agent_id, ["email.send", "web.scrape"]).status_code == 200
    before = agent_state("chiefofstaff", shared.agent_id)
    r = put_grants(user, shared.agent_id, ["email.send"])
    assert r.status_code == 403 and "shared_agent_admin_only" in r.text, r.text
    r = post_grant(user, shared.agent_id, "datagov.search")
    assert r.status_code == 403 and "shared_agent_admin_only" in r.text, r.text
    assert agent_state("chiefofstaff", shared.agent_id) == before


def test_an_admin_group_caseworker_cannot_widen_an_ownerless_agent_past_their_role():
    """The admin group may edit shared agents, but the role cap still applies: without it an admin-group
    caseworker would grant the shared agent the mailing list."""
    shared = _ownerless({"email.send"}, kind="email")
    assert post_grant("admin-caseworker", shared.agent_id, "datagov.search").status_code == 200  # control
    before = agent_state("admin-caseworker", shared.agent_id)
    assert post_grant("admin-caseworker", shared.agent_id, "email.send_list").status_code == 403
    assert put_grants("admin-caseworker", shared.agent_id, ["email.send", "email.send_list"]).status_code == 403
    assert agent_state("admin-caseworker", shared.agent_id) == before


# ---- the picker never shows it, the server still refuses ----------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(TOOL_REGISTRY))
def test_the_server_agrees_with_the_picker_for_every_tool_when_called_directly(tool):
    """The picker greys out what the role forbids, but a script can POST anything. Without the server-side cap the
    catalog would be a suggestion: each tool the catalog marks unavailable must be refused, each available one
    accepted."""
    r = client.get(f"{API}/agent-permissions", params={"kind": "custom"}, headers=login("maya"))
    assert r.status_code == 200, r.text
    entry = next(t for t in r.json()["tools"] if t["name"] == tool)
    created = create("maya", kind="custom", tool_grants=[tool])
    if entry["allowed_for_role"]:
        assert created.status_code == 201, (tool, created.text)
    else:
        assert created.status_code == 403, (tool, created.text)


# ---- the role changes after the agent exists ----------------------------------------------------------------------


def _social_agent(owner="admin-comms"):
    """A social agent owned by an admin-group communications director (an admin can mint workload tokens)."""
    aid = create_ok(owner, kind="social", name="Social")["agent_id"]
    return aid, mint(owner, aid)


def test_a_demoted_owners_agent_is_denied_at_its_next_tool_call(production_roles, audit):
    """Granting only checked the role at creation. Without the runtime check an owner demoted to intern keeps
    posting and replying through the agent they built as a communications director."""
    aid, token = _social_agent()
    first = call("admin-comms", token, "social.reply", REPLY)
    assert first.status_code == 200 and first.json()["decision"] == "pending_approval", first.text  # control

    set_role(production_roles, "admin-comms", "intern")
    r = call("admin-comms", token, "social.reply", REPLY)
    assert r.status_code == 403, r.text
    assert "role_not_allowed" in r.text
    assert any(e.get("decision") == "deny" and e.get("tool") == "social.reply" and e.get("agent_id") == aid for e in audit)
    # still denied with a fresh token, and for the other granted tool
    token2 = mint("admin-comms", aid)
    assert call("admin-comms", token2, "social.post", {"platform": "x", "text": "hello"}).status_code == 403


def test_the_role_cap_removes_only_what_the_new_role_lacks(production_roles):
    """A demotion must not turn into a blanket lockout of read tools the new role still has (that would hide a
    cap that is really 'deny everything'), and it must still remove the tool it lacks."""
    aid = create_ok("admin-comms", kind="custom", tool_grants=["web.scrape", "social.reply"])["agent_id"]
    token = mint("admin-comms", aid)
    set_role(production_roles, "admin-comms", "intern")
    assert call("admin-comms", token, "social.reply", REPLY).status_code == 403
    assert call("admin-comms", token, "web.scrape", SCRAPE).status_code == 200  # intern may read public pages


@pytest.mark.parametrize("change", ["offboarded", "role-cleared"])
def test_an_offboarded_owner_or_one_with_no_role_is_denied_on_every_tool(production_roles, change):
    """If runtime treated 'inactive' or 'no officeRole' as unrestricted, an offboarded staffer's agents would keep
    running with full tool access."""
    aid = create_ok("admin-comms", kind="custom", tool_grants=["web.scrape", "social.reply"])["agent_id"]
    token = mint("admin-comms", aid)
    assert call("admin-comms", token, "web.scrape", SCRAPE).status_code == 200  # control
    if change == "offboarded":
        production_roles.set_staffer_active("admin-comms", False)
    else:
        set_role(production_roles, "admin-comms", None)
    token = mint("admin-comms", aid)  # fresh workload token: it is the role check, not an old token
    for tool, args in (("web.scrape", SCRAPE), ("social.reply", REPLY)):
        r = call("admin-comms", token, tool, args)
        assert r.status_code == 403 and "role_not_allowed" in r.text, (tool, r.status_code, r.text)


def test_approving_a_held_draft_after_the_owner_was_demoted_is_403(production_roles):
    """A held social reply could sit in the approvals queue while the owner is demoted; without the role check on
    approve, an admin could still publish it under the old, larger role."""
    aid, token = _social_agent()
    approvals = []
    for text in ("one", "two"):
        r = call("admin-comms", token, "social.reply", {**REPLY, "text": f"Thanks, {text}"})
        assert r.status_code == 200, r.text
        approvals.append(r.json()["approval_id"])
    ok = client.post(f"{API}/approvals/{approvals[0]}/approve", json={}, headers=login("admin-comms"))
    assert ok.status_code == 200, ok.text  # control: approving works while the role allows it

    set_role(production_roles, "admin-comms", "intern")
    r = client.post(f"{API}/approvals/{approvals[1]}/approve", json={}, headers=login("admin-comms"))
    assert r.status_code == 403, r.text
    still = client.get(f"{API}/approvals/{approvals[1]}", headers=login("admin-comms")).json()
    assert still["status"] == "pending"  # not executed, not consumed


# ---- ownerless agents take the role of whoever is acting --------------------------------------------------------


def test_an_ownerless_agents_tool_call_is_capped_by_the_staffer_making_it():
    """An ownerless agent has no owner role to cap by. Without 'use the acting staffer's role', an intern could
    drive the shared agent's email.send_list or email.send with the agent's wider grants."""
    shared = _ownerless({"email.send", "email.send_list"}, kind="custom")
    chief_token = mint("chiefofstaff", shared.agent_id)
    r = call("chiefofstaff", chief_token, "email.send_list", MAIL_LIST)
    assert r.status_code == 200 and r.json()["decision"] == "pending_approval", r.text  # control: the chief may

    intern_token = mint("admin-intern", shared.agent_id)
    r = call("admin-intern", intern_token, "email.send", MAIL)
    assert r.status_code == 403 and "role_not_allowed" in r.text, r.text

    caseworker_token = mint("admin-caseworker", shared.agent_id)
    ok = call("admin-caseworker", caseworker_token, "email.send", MAIL)
    assert ok.status_code == 200 and ok.json()["decision"] == "pending_approval", ok.text  # inside their role
    r = call("admin-caseworker", caseworker_token, "email.send_list", MAIL_LIST)
    assert r.status_code == 403 and "role_not_allowed" in r.text, r.text


def test_an_ownerless_agents_run_is_capped_by_the_staffer_who_started_it(production_roles):
    """For a run (the Bedrock runner's path) the cap is the staffer in the run record. Without that, a run started
    by an intern would use the ownerless agent's full grants."""
    shared = _ownerless({"email.send", "email.send_list", "web.scrape"}, kind="custom")

    def run_as(created_by, tool, args):
        run = agents_api.runs.add(new_run(shared, "task", "model", created_by))
        token = agents_api._issue_run_token(shared.agent_id, run.run_id)
        return agents_api.gateway_tool_call(token, tool, args, run_id=run.run_id)

    assert run_as("chiefofstaff", "email.send_list", MAIL_LIST)["decision"] == "pending_approval"  # control
    assert run_as("maya", "email.send", MAIL)["decision"] == "pending_approval"  # caseworker may email
    for created_by, tool, args in (
        ("maya", "email.send_list", MAIL_LIST),
        ("aws", "email.send", MAIL),
        ("aws", "email.send_list", MAIL_LIST),
    ):
        with pytest.raises(HTTPException) as denied:
            run_as(created_by, tool, args)
        assert denied.value.status_code == 403, (created_by, tool)
    assert run_as("aws", "web.scrape", SCRAPE)["decision"] == "allow"  # the intern's own tools still work


def test_an_ownerless_agent_with_no_run_and_no_acting_staffer_is_denied(production_roles):
    """With neither an owner, a run creator nor an acting user there is no role to cap by. Without 'deny when
    unknown', that call would run unrestricted."""
    shared = _ownerless({"email.send", "web.scrape"}, kind="custom")
    token = agents_api._issue_run_token(shared.agent_id, None)
    with pytest.raises(HTTPException) as denied:
        agents_api.gateway_tool_call(token, "web.scrape", SCRAPE)
    assert denied.value.status_code == 403
    unknown_run = agents_api.runs.add(new_run(shared, "task", "model", None))  # run with no creator
    token = agents_api._issue_run_token(shared.agent_id, unknown_run.run_id)
    with pytest.raises(HTTPException) as denied:
        agents_api.gateway_tool_call(token, "web.scrape", SCRAPE, run_id=unknown_run.run_id)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        agents_api.gateway_tool_call(token, "web.scrape", SCRAPE, run_id="run_does_not_exist")
    assert denied.value.status_code == 403


# ---- workload tokens ----------------------------------------------------------------------------------------------


def test_a_workload_token_is_401_token_revoked_after_a_grant_revoke():
    """Without the token_version bump on revoke, a token issued before the revoke would keep the revoked power."""
    aid = create_ok("chiefofstaff", kind="custom", tool_grants=["web.scrape", "datagov.search"])["agent_id"]
    token = mint("chiefofstaff", aid)
    assert call("chiefofstaff", token, "web.scrape", SCRAPE).status_code == 200  # control
    r = client.post(f"{API}/agents/{aid}/grants/web.scrape/revoke", headers=login("chiefofstaff"))
    assert r.status_code == 200, r.text
    r = call("chiefofstaff", token, "web.scrape", SCRAPE)
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["error"] == "token_revoked"
    assert call("chiefofstaff", mint("chiefofstaff", aid), "web.scrape", SCRAPE).status_code == 403  # fresh token: not granted


def test_a_workload_token_is_401_token_revoked_after_the_grants_are_replaced():
    """The same token-version guard on PUT /grants: replacing the grant set must not leave old tokens valid."""
    aid = create_ok("maya", kind="custom", tool_grants=["web.scrape"])["agent_id"]
    # maya (staffer group) cannot mint; the chief cannot see maya's agent, so use an admin-group owner
    admin_aid = create_ok("admin-caseworker", kind="custom", tool_grants=["web.scrape", "datagov.search"])["agent_id"]
    token = mint("admin-caseworker", admin_aid)
    assert call("admin-caseworker", token, "web.scrape", SCRAPE).status_code == 200  # control
    assert put_grants("admin-caseworker", admin_aid, ["datagov.search"]).status_code == 200
    r = call("admin-caseworker", token, "datagov.search", {"q": "census"})
    assert r.status_code == 401 and r.json()["detail"]["error"] == "token_revoked", r.text
    assert aid  # (maya's agent only proves a staffer can create; it is not used further)


# ---- omitted tool_grants = the recommendation, not the old defaults ------------------------------------------------

RECOMMENDED_ON_CREATE = [
    pytest.param("chiefofstaff", "email", ["email.send"], id="chief-email-no-mailing-list"),
    pytest.param("maya", "email", ["email.send"], id="caseworker-email"),
    pytest.param("scuacm", "social", ["social.post", "social.reply"], id="comms-social"),
    pytest.param("aws", "research", ["census.query", "datagov.search", "noaa.climate", "nasa.search", "nps.parks", "web.scrape"], id="intern-research-capped"),
    pytest.param("a10", "market_research", ["report.save", "sam.entity_lookup", "usaspending.search", "web.scrape"], id="aide-market-research"),
    pytest.param("maya", "market_research", ["report.save", "web.scrape"], id="caseworker-market-research-capped"),
    pytest.param("chiefofstaff", "custom", [], id="custom-starts-empty"),
]


@pytest.mark.parametrize("user,kind,expected", RECOMMENDED_ON_CREATE)
def test_omitting_tool_grants_gives_the_recommended_set_capped_by_the_role(user, kind, expected):
    """The old default for an email agent included email.send_list (mail every constituent). Without 'omitted =
    recommended' a new agent would start with more than least privilege, or with tools its creator's role lacks."""
    body = _body(kind=kind)
    del body["tool_grants"]
    r = client.post(f"{API}/agents", json=body, headers=login(user))
    assert r.status_code == 201, r.text
    assert r.json()["tool_grants"] == sorted(expected)
