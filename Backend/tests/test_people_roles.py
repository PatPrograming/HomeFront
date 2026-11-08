"""Admin "People & roles" (Backend/app/routers/people.py): list people and roles, assign a role or group, edit
what a role may do. Real Cognito token checks (`cognito_login` + signed test tokens), moto DynamoDB, a fake
Cognito directory, and the gateway for "applies on the next call". No AWS.

Each escalation test names the guard it proves; removing that guard makes it fail."""

from __future__ import annotations

import time

import pytest
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import MandateClaims
from mandate_contracts.ddb import SessionItem, StafferItem
from mandate_contracts.roles import ROLE_TOOLS

from app.services.directory import DirectoryUser, set_directory

from .helpers import auth
from .test_gateway_api import SECRET, _env, client, gateway, store, table  # noqa: F401 (fixtures)

pytestmark = pytest.mark.usefixtures("cognito_login")

CHIEF = auth("chiefofstaff", groups=("admin",))
MAYA = auth("maya", groups=("staffer",))
SID = "sid-people-1"


class FakeDirectory:
    """Cognito as the routes see it: users and their staff group."""

    def __init__(self, users):
        self.users = {u.username: u for u in users}
        self.calls = []

    def list_users(self):
        return sorted(self.users.values(), key=lambda u: u.username)

    def set_group(self, username, group):
        self.calls.append((username, group))
        u = self.users[username]
        self.users[username] = DirectoryUser(u.username, u.email, u.enabled, group)


@pytest.fixture
def directory():
    d = FakeDirectory(
        [
            DirectoryUser("chiefofstaff", "chief@example.gov", True, "admin"),
            DirectoryUser("maya", "maya@example.gov", True, "staffer"),
            DirectoryUser("dana", None, True, "staffer"),
            DirectoryUser("newbie", "new@example.gov", True, "staffer"),  # no STAFFER item yet
        ]
    )
    set_directory(d)
    yield d
    set_directory(None)


@pytest.fixture
def maya_session(store):
    store.put_session(
        SID,
        SessionItem(status="active", agent="agent-1", acting_for="maya", office="rep-x", mode="official",
                    created_at="2026-11-08T10:00:00+00:00"),
    )
    now = int(time.time())
    claims = MandateClaims(sub="agent-1", act="maya", office="rep-x", mode="official", sid=SID, iat=now, exp=now + 900)
    return mint_mandate_token(claims, SECRET)


def _snapshot(store, directory):
    return (
        {k: v.model_dump() for k, v in store.list_staffers().items()},
        {r: store.get_role_item(r) for r in ROLE_TOOLS},
        dict(directory.users),
        len(store.admin_events()),
    )


ADMIN_ROUTES = [
    ("get", "/api/v1/admin/people", None),
    ("get", "/api/v1/admin/roles", None),
    ("get", "/api/v1/admin/audit", None),
    ("post", "/api/v1/admin/roles/intern/preview", {"tools": ["web_scrape"]}),
    ("put", "/api/v1/admin/roles/intern", {"tools": list(ROLE_TOOLS["chief_of_staff"])}),
    ("put", "/api/v1/admin/people/maya", {"officeRole": "chief_of_staff"}),
    ("put", "/api/v1/admin/people/maya", {"group": "admin"}),
]


# ---- who may use the routes ----------------------------------------------------


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_a_staffer_cannot_use_any_admin_route(client, store, directory, method, path, body):
    """Guard: require_admin on every route. A staffer promoting herself is the case that matters most."""
    before = _snapshot(store, directory)
    r = getattr(client, method)(path, headers=MAYA, **({"json": body} if body else {}))
    assert r.status_code == 403, r.text
    assert _snapshot(store, directory) == before


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_no_or_forged_login_gets_401(client, directory, method, path, body):
    for headers in ({}, {"Authorization": "Bearer not-a-token"}):
        r = getattr(client, method)(path, headers=headers, **({"json": body} if body else {}))
        assert r.status_code == 401, r.text


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_an_admin_token_is_not_enough_once_dynamodb_says_staffer_or_inactive(client, store, directory, method, path, body):
    """Guard: require_people_admin's fresh STAFFER check. A demoted or offboarded admin's token is still valid
    for up to an hour; the routes must refuse it now."""
    for item in (
        StafferItem(active=True, office="rep-x", role="staffer", office_role="chief_of_staff"),
        StafferItem(active=False, office="rep-x", role="admin", office_role="chief_of_staff"),
    ):
        store.put_staffer("chiefofstaff", item)
        r = getattr(client, method)(path, headers=CHIEF, **({"json": body} if body else {}))
        assert r.status_code == 403, r.text


def test_a_token_in_the_admin_group_for_someone_with_no_staffer_item_is_refused(client, directory):
    r = client.get("/api/v1/admin/people", headers=auth("ghost", groups=("admin",)))
    assert r.status_code == 403


# ---- reading ---------------------------------------------------------------------


def test_people_merges_cognito_and_dynamodb(client, directory):
    people = {p["username"]: p for p in client.get("/api/v1/admin/people", headers=CHIEF).json()["people"]}
    assert people["maya"]["officeRole"] == "caseworker" and people["maya"]["roleLabel"] == "Caseworker"
    assert people["maya"]["group"] == "staffer" and people["chiefofstaff"]["group"] == "admin"
    assert people["newbie"]["officeRole"] is None and people["newbie"]["roleLabel"] == "No role"
    assert people["retired"]["inDirectory"] is False and people["retired"]["active"] is False


def test_roles_show_every_role_and_what_it_may_do(client, directory):
    body = client.get("/api/v1/admin/roles", headers=CHIEF).json()
    roles_by_id = {r["id"]: r for r in body["roles"]}
    assert [r["label"] for r in body["roles"]] == [
        "Chief of Staff", "Caseworker", "Communications Director", "Legislative Aide", "Intern"
    ]
    assert set(roles_by_id["intern"]["tools"]) == set(ROLE_TOOLS["intern"])
    assert roles_by_id["caseworker"]["people"] == ["dana", "maya", "retired"]
    assert "records_bulk_export" not in roles_by_id["chief_of_staff"]["assignable"]
    tools = {t["name"]: t for t in body["tools"]}
    assert tools["records_bulk_export"]["alwaysDenied"] is True
    assert tools["email_send"]["action"] == "send email for the office"


# ---- assigning a role ------------------------------------------------------------


def test_assigning_a_role_applies_to_the_running_session_on_the_next_call(client, store, gateway, directory, maya_session):
    assert gateway.guarded("case_read", {"case_id": "case-1"}, maya_session).allowed is True
    r = client.put("/api/v1/admin/people/maya", headers=CHIEF, json={"officeRole": "intern"})
    assert r.status_code == 200 and r.json()["officeRole"] == "intern"
    out = gateway.guarded("case_read", {"case_id": "case-1"}, maya_session)  # same token, same session
    assert out.allowed is False and out.event.policy_ids == ["role-case-read"]
    event = store.admin_events()[0]
    assert (event.type, event.actor, event.target, event.before, event.after) == (
        "role_assigned", "chiefofstaff", "maya", "caseworker", "intern"
    )


@pytest.mark.parametrize("role", ["senator", "admin", "staffer", "Chief_Of_Staff", "intern ", ""])
def test_assigning_a_role_that_does_not_exist_is_refused(client, store, directory, role):
    """Guard: the officeRole check in assign()."""
    before = _snapshot(store, directory)
    r = client.put("/api/v1/admin/people/maya", headers=CHIEF, json={"officeRole": role})
    assert r.status_code == 400, r.text
    assert _snapshot(store, directory) == before


def test_assigning_a_role_to_someone_new_creates_their_staffer_item_in_the_admins_office(client, store, directory):
    r = client.put("/api/v1/admin/people/newbie", headers=CHIEF, json={"officeRole": "legislative_aide"})
    assert r.status_code == 200, r.text
    item = store.get_staffer("newbie")
    assert (item.office, item.role, item.office_role, item.active) == ("rep-x", "staffer", "legislative_aide", True)


def test_an_unknown_person_is_404(client, store, directory):
    assert client.put("/api/v1/admin/people/nobody", headers=CHIEF, json={"officeRole": "intern"}).status_code == 404
    assert store.get_staffer("nobody") is None


# ---- groups and the last admin ---------------------------------------------------


def test_the_last_admin_cannot_demote_themselves(client, store, directory):
    """Guard: the last-admin check. Without it the office is left with nobody who can manage roles."""
    before = _snapshot(store, directory)
    r = client.put("/api/v1/admin/people/chiefofstaff", headers=CHIEF, json={"group": "staffer"})
    assert r.status_code == 409 and "last admin" in r.json()["detail"]
    assert _snapshot(store, directory) == before and directory.calls == []


def test_a_disabled_admin_does_not_count_as_another_admin(client, directory):
    directory.users["old"] = DirectoryUser("old", None, False, "admin")
    r = client.put("/api/v1/admin/people/chiefofstaff", headers=CHIEF, json={"group": "staffer"})
    assert r.status_code == 409


def test_an_admin_who_cannot_use_the_admin_routes_does_not_count_as_another_admin(client, store, directory):
    """Guard: the last-admin check counts only working admins (Cognito admin AND an active admin STAFFER item).
    Promoting someone with no STAFFER item, or keeping an offboarded admin in Cognito, must not let the only working
    admin demote themselves."""
    assert client.put("/api/v1/admin/people/newbie", headers=CHIEF, json={"group": "admin"}).status_code == 200
    directory.users["olda"] = DirectoryUser("olda", None, True, "admin")
    store.put_staffer("olda", StafferItem(active=False, office="rep-x", role="admin"))
    before = _snapshot(store, directory)
    r = client.put("/api/v1/admin/people/chiefofstaff", headers=CHIEF, json={"group": "staffer"})
    assert r.status_code == 409 and "last admin" in r.json()["detail"]
    assert _snapshot(store, directory) == before
    assert client.get("/api/v1/admin/people", headers=CHIEF).status_code == 200


def test_with_another_admin_a_demotion_works_and_cuts_the_old_token_off_at_once(client, store, directory):
    assert client.put("/api/v1/admin/people/maya", headers=CHIEF, json={"group": "admin"}).status_code == 200
    assert store.get_staffer("maya").role == "admin" and directory.users["maya"].group == "admin"
    r = client.put("/api/v1/admin/people/chiefofstaff", headers=CHIEF, json={"group": "staffer"})
    assert r.status_code == 200, r.text
    assert directory.users["chiefofstaff"].group == "staffer"
    # The chief's access token still says "admin", but DynamoDB no longer does.
    assert client.get("/api/v1/admin/people", headers=CHIEF).status_code == 403
    types = [e.type for e in store.admin_events()]
    assert types.count("group_changed") == 2


# ---- forged fields ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/admin/people/maya", {"officeRole": "intern", "role": "admin"}),
        ("/api/v1/admin/people/maya", {"group": "admin", "username": "chiefofstaff"}),
        ("/api/v1/admin/people/maya", {"officeRole": "intern", "office": "rep-y"}),
        ("/api/v1/admin/people/maya", {"officeRole": "intern", "actor": "someone-else"}),
        ("/api/v1/admin/roles/intern", {"tools": ["web_scrape"], "role": "chief_of_staff"}),
        ("/api/v1/admin/roles/intern", {"tools": ["web_scrape"], "updatedBy": "nobody"}),
        ("/api/v1/admin/people/maya", {"group": "superadmin"}),
    ],
)
def test_a_forged_field_in_the_body_is_rejected(client, store, directory, path, body):
    """Guard: extra="forbid" on the bodies. Only officeRole/group and tools are accepted."""
    before = _snapshot(store, directory)
    assert client.put(path, headers=CHIEF, json=body).status_code == 422
    assert _snapshot(store, directory) == before


def test_a_forged_role_in_a_staffers_session_request_is_ignored(client, directory):
    from app.main import app
    from app.routers import sessions

    from .test_gateway_api import MAYA as MAYA_IDENTITY

    app.dependency_overrides[sessions.require_staff] = lambda: MAYA_IDENTITY
    body = client.post("/api/v1/sessions", json={"role": "chief_of_staff", "officeRole": "chief_of_staff"}).json()
    assert body["role"] == "caseworker" and "social_post" not in body["allowedTools"]


# ---- what a role may do ----------------------------------------------------------


def test_editing_a_role_applies_mid_session_and_is_audited(client, store, gateway, directory, maya_session):
    assert gateway.guarded("case_read", {"case_id": "case-1"}, maya_session).allowed is True
    tools = [t for t in ROLE_TOOLS["caseworker"] if t != "case_read"]
    r = client.put("/api/v1/admin/roles/caseworker", headers=CHIEF, json={"tools": tools})
    assert r.status_code == 200, r.text
    assert r.json()["changes"] == ["Every Caseworker's assistant will no longer be able to open constituent case files."]
    out = gateway.guarded("case_read", {"case_id": "case-1"}, maya_session)
    assert out.allowed is False and out.event.policy_ids == ["role-case-read"]
    event = store.admin_events()[0]
    assert event.type == "role_tools_changed" and event.target == "caseworker" and "case_read" in event.before
    # The console's list for a new session follows the stored list too.
    roles_view = {x["id"]: x for x in client.get("/api/v1/admin/roles", headers=CHIEF).json()["roles"]}
    assert roles_view["caseworker"]["customized"] is True and "case_read" not in roles_view["caseworker"]["tools"]


@pytest.mark.parametrize("role", list(ROLE_TOOLS))
def test_a_role_can_never_be_given_an_always_denied_tool(client, store, directory, role):
    """Guard: validate_role_tools' always-denied check."""
    before = _snapshot(store, directory)
    for path in (f"/api/v1/admin/roles/{role}", f"/api/v1/admin/roles/{role}/preview"):
        call = client.put if not path.endswith("preview") else client.post
        r = call(path, headers=CHIEF, json={"tools": ["web_scrape", "records_bulk_export"]})
        assert r.status_code == 400 and "blocked for everyone" in r.json()["detail"]
    assert _snapshot(store, directory) == before


@pytest.mark.parametrize("role", [r for r in ROLE_TOOLS if r != "chief_of_staff"])
def test_the_locked_mailing_list_tool_can_never_be_given_below_the_chief_of_staff(client, store, directory, role):
    """Guard: LOCKED_TOOLS in validate_role_tools (lead ruling). The server refuses whatever the client sends."""
    before = _snapshot(store, directory)
    tools = [*ROLE_TOOLS[role], "email_send_list"]
    for call, path in ((client.put, f"/api/v1/admin/roles/{role}"), (client.post, f"/api/v1/admin/roles/{role}/preview")):
        r = call(path, headers=CHIEF, json={"tools": tools})
        assert r.status_code == 400 and "locked by policy" in r.json()["detail"], r.text
    assert _snapshot(store, directory) == before


def test_the_chief_of_staff_keeps_the_locked_tool_and_the_editor_is_told_it_is_locked(client, directory):
    tools = {t["name"]: t for t in client.get("/api/v1/admin/roles", headers=CHIEF).json()["tools"]}
    assert tools["email_send_list"]["lockedTo"] == ["Chief of Staff"] and tools["web_scrape"]["lockedTo"] == []
    r = client.put("/api/v1/admin/roles/chief_of_staff", headers=CHIEF, json={"tools": ["email_send_list"]})
    assert r.status_code == 200


def test_a_locked_tool_written_straight_to_dynamodb_still_does_not_reach_an_intern(client, store, table, gateway, directory, maya_session):
    from mandate_contracts.ddb import role_key

    table.put_item(Item={**role_key("caseworker"), "tools": ["email_send_list"], "updatedBy": "x", "updatedAt": "t"})
    assert "email_send_list" not in store.get_role_tools("caseworker")
    out = gateway.guarded("email_send_list", {"subject": "s", "body": "b"}, maya_session)
    assert out.allowed is False


@pytest.mark.parametrize("tools", [["web_scrape", "web_scrape"], ["Web_Scrape"], ["web.scrape"], ["anything"]])
def test_unknown_or_duplicate_tools_are_refused(client, store, directory, tools):
    assert client.put("/api/v1/admin/roles/intern", headers=CHIEF, json={"tools": tools}).status_code == 400
    assert store.get_role_item("intern") is None


def test_editing_a_role_that_does_not_exist_is_404(client, store, directory):
    assert client.put("/api/v1/admin/roles/senator", headers=CHIEF, json={"tools": []}).status_code == 404
    assert store.get_role_item("senator") is None


def test_preview_says_what_a_change_allows_and_changes_nothing(client, store, directory):
    before = _snapshot(store, directory)
    tools = [t for t in ROLE_TOOLS["intern"] if t != "datagov_search"] + ["email_send"]
    r = client.post("/api/v1/admin/roles/intern/preview", headers=CHIEF, json={"tools": tools})
    assert r.status_code == 200
    body = r.json()
    assert body["changes"] == [
        "Every Intern's assistant will be able to send email for the office.",
        "Every Intern's assistant will no longer be able to search public government data.",
    ]
    assert body["affects"] == []
    assert _snapshot(store, directory) == before


def test_the_audit_log_lists_every_change_newest_first(client, directory):
    client.put("/api/v1/admin/people/maya", headers=CHIEF, json={"officeRole": "intern"})
    client.put("/api/v1/admin/roles/intern", headers=CHIEF, json={"tools": ["web_scrape"]})
    events = client.get("/api/v1/admin/audit", headers=CHIEF).json()["events"]
    assert [e["type"] for e in events] == ["role_tools_changed", "role_assigned"]
    assert all(e["actor"] == "chiefofstaff" and e["summary"] for e in events)
