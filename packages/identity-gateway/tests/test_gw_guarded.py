"""Gateway.guarded(): the single path every tool call takes."""

from __future__ import annotations

import json
import time

import pytest
from identity_gateway.authz import AuthzDecision
from identity_gateway.gateway import Gateway, get_gateway, set_gateway
from identity_gateway.tokens import InvalidToken
from mandate_contracts import TOOLS, GuardrailFlag, cedar
from mandate_contracts.ddb import SessionItem

REVOKED = "session revoked / delegator inactive"
DENIED = "denied by policy"

KNOWN_ARGS = {
    "inbox_read": {"email_id": "email-1"},
    "case_read": {"case_id": "case-1"},
    "datagov_search": {"query": "housing"},
    "web_scrape": {"url": "https://example.gov"},
    "email_send": {"to": "jane.doe@example.com", "subject": "Hi", "body": "Update", "in_reply_to": "email-trusted"},
    "social_post": {"account": "official", "text": "Town hall Tuesday"},
}


def _ctx(req):
    """Decode the AVP context map the gateway built into plain python values."""
    out = {}
    for key, value in req.context["contextMap"].items():
        out[key] = next(iter(value.values()))
    return out


def _only_event(events_of, store):
    events = events_of(store)
    assert len(events) == 1, f"expected exactly one DecisionEvent, got {len(events)}"
    return events[0]


class AllowEverything:
    def is_authorized(self, req):
        return AuthzDecision(allowed=True, policy_ids=["allow-all"])


class Exploding:
    def is_authorized(self, req):
        raise TimeoutError("AVP timed out")


# ---- ALLOW path -----------------------------------------------------------


def test_allowed_case_read_returns_the_case_and_writes_one_allow_event(
    gateway, store, make_token, world, events_of
):
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    assert out.allowed is True
    blob = json.dumps(out.result)
    assert "Jane Doe" in blob and "Pothole on Main St" in blob
    e = _only_event(events_of, store)
    assert out.event == e
    assert (e.decision, e.reason) == ("ALLOW", "allowed")
    assert e.policy_ids == ["case-read-same-office"]
    assert (e.sid, e.agent, e.acting_for, e.office, e.tool) == (
        world.sid, world.agent, "maya", "rep-x", "case_read",
    )
    assert e.id and e.ts


def test_each_call_writes_its_own_event_with_a_unique_id(gateway, store, make_token, world, events_of):
    for _ in range(3):
        gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    events = events_of(store)
    assert len(events) == 3
    assert len({e.id for e in events}) == 3


def test_inbox_read_returns_the_email_item(gateway, make_token, world):
    out = gateway.guarded("inbox_read", {"email_id": world.email_id}, make_token())
    assert out.allowed is True
    blob = json.dumps(out.result)
    assert world.email_body in blob and "Road repair" in blob and "citizen@example.com" in blob


def test_allowed_email_send_runs_the_mcp_tools_handler_and_is_simulated(
    trusted_replies, gateway, make_token, handler_spy, world
):
    out = gateway.guarded("email_send", KNOWN_ARGS["email_send"], make_token())
    assert out.allowed is True
    assert out.result["simulated"] is True
    assert handler_spy.calls, "email_send did not go through an mcp_tools handler"


@pytest.mark.parametrize("tool", ["datagov_search", "web_scrape", "social_post"])
def test_other_allowed_tools_go_through_mcp_tools(maya_posts, trusted_replies, gateway, make_token, handler_spy, tool):
    out = gateway.guarded(tool, KNOWN_ARGS[tool], make_token())
    assert out.allowed is True and out.result
    assert handler_spy.calls


# ---- facts come from DynamoDB and the token, never from tool input --------


def test_case_read_of_another_offices_case_is_denied_without_returning_it(
    gateway, store, make_token, world, events_of
):
    out = gateway.guarded("case_read", {"case_id": world.case_y}, make_token())
    assert out.allowed is False
    assert out.result is None
    e = _only_event(events_of, store)
    assert (e.decision, e.reason) == ("DENY", DENIED)
    assert "case-read-same-office" not in e.policy_ids


def test_case_resource_office_comes_from_the_case_item_not_args(
    gateway, authorizer, make_token, world
):
    out = gateway.guarded(
        "case_read", {"case_id": world.case_y, "office": "rep-x"}, make_token()
    )
    assert out.allowed is False
    (req,) = authorizer.requests
    assert req.resource == cedar.case_entity(world.case_y, "rep-y")


def test_agent_supplied_office_cannot_block_an_own_office_read(gateway, make_token, world):
    out = gateway.guarded("case_read", {"case_id": world.case_x, "office": "rep-y"}, make_token())
    assert out.allowed is True


def test_principal_office_and_mode_come_from_the_session_item(
    gateway, store, authorizer, make_token, world
):
    store.put_session(
        world.sid,
        SessionItem(
            status="active",
            agent=world.agent,
            acting_for="maya",
            office="rep-y",
            mode="campaign",
            created_at="2026-11-07T10:00:00Z",
        ),
    )
    # the token still claims rep-x / official
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token(office="rep-x"))
    (req,) = authorizer.requests
    assert (req.office, req.mode) == ("rep-y", "campaign")
    assert out.allowed is False  # rep-y principal reading a rep-x case


def test_authz_request_identifies_the_agent_and_tool(gateway, authorizer, make_token, world):
    gateway.guarded("datagov_search", KNOWN_ARGS["datagov_search"], make_token())
    (req,) = authorizer.requests
    assert req.agent_id == world.agent
    assert req.tool == "datagov_search"
    assert req.resource == cedar.tool_entity("datagov_search")


@pytest.mark.parametrize("tool", list(KNOWN_ARGS))
def test_context_always_carries_all_three_keys(gateway, authorizer, make_token, tool):
    gateway.guarded(tool, KNOWN_ARGS[tool], make_token())
    (req,) = authorizer.requests
    assert set(req.context["contextMap"]) == {"delegatorActive", "recipientKnown", "account"}
    assert _ctx(req)["delegatorActive"] is True


@pytest.mark.parametrize(
    ("to", "known"),
    [
        ("jane.doe@example.com", True),  # constituent on file, stored mixed-case
        ("JANE.DOE@EXAMPLE.COM", True),  # lookup is case-insensitive
        ("bob@other.org", False),  # a constituent of a rep-y case is not on file for rep-x
        ("staff@house.gov", True),
        ("x@evil.gov.example.com", False),  # .gov appears but is not the domain suffix
        ("a@b.gov, c@evil.com", False),  # two addresses: not a single .gov address
        ("c@evil.com, a@b.gov", False),
        ("stranger@gmail.com", False),
        ("somebody@evilgov", False),
        ("", False),
    ],
)
def test_recipient_known_only_for_constituents_on_file_or_single_gov_address(
    gateway, authorizer, make_token, to, known
):
    gateway.guarded("email_send", {"to": to, "subject": "s", "body": "b"}, make_token())
    (req,) = authorizer.requests
    assert _ctx(req)["recipientKnown"] is known


def test_email_to_an_unknown_recipient_is_denied_and_nothing_is_sent(
    gateway, store, make_token, handler_spy, events_of
):
    args = {"to": "stranger@gmail.com", "subject": "s", "body": "b"}
    out = gateway.guarded("email_send", args, make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []
    e = _only_event(events_of, store)
    assert (e.decision, e.reason) == ("DENY", DENIED)


def test_social_post_account_in_context_is_the_argument(gateway, authorizer, make_token):
    gateway.guarded("social_post", {"account": "campaign", "text": "vote"}, make_token())
    (req,) = authorizer.requests
    assert _ctx(req)["account"] == "campaign"


def test_campaign_post_is_denied_by_the_forbid_policy_and_not_published(
    maya_posts, gateway, make_token, handler_spy
):
    out = gateway.guarded("social_post", {"account": "campaign", "text": "vote"}, make_token())
    assert out.allowed is False and out.result is None
    assert out.event.reason == DENIED
    assert out.event.policy_ids == ["forbid-campaign-post"]
    assert handler_spy.calls == []


# ---- session and delegator checks ----------------------------------------


def _revoke(store, world):
    s = store.get_session(world.sid)
    store.put_session(world.sid, s.model_copy(update={"status": "revoked"}))


def test_revoked_session_is_denied_and_the_tool_does_not_run(
    gateway, store, authorizer, make_token, handler_spy, world, events_of
):
    _revoke(store, world)
    out = gateway.guarded("email_send", KNOWN_ARGS["email_send"], make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []
    assert authorizer.requests == []  # never even asked Cedar
    e = _only_event(events_of, store)
    assert (e.decision, e.reason) == ("DENY", REVOKED)
    assert (e.sid, e.acting_for, e.agent, e.office) == (world.sid, "maya", world.agent, "rep-x")


def test_unknown_session_is_denied(gateway, make_token, handler_spy):
    out = gateway.guarded("datagov_search", KNOWN_ARGS["datagov_search"], make_token(sid="nope"))
    assert out.allowed is False
    assert out.event.reason == REVOKED
    assert handler_spy.calls == []


def test_session_acting_for_someone_else_is_denied(gateway, make_token, world):
    # chiefofstaff's session id presented with a token that says it acts for maya
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token(sid=world.chief_sid))
    assert out.allowed is False and out.result is None
    assert out.event.reason == REVOKED


def test_inactive_staffer_is_denied_even_with_an_active_session(
    gateway, store, make_token, handler_spy
):
    store.set_staffer_active("maya", False)
    out = gateway.guarded("email_send", KNOWN_ARGS["email_send"], make_token())
    assert out.allowed is False
    assert out.event.reason == REVOKED
    assert handler_spy.calls == []


def test_missing_staffer_is_denied(gateway, store, make_token, world):
    store.put_session(
        "sid-ghost",
        SessionItem(
            status="active", agent=world.agent, acting_for="ghost", office="rep-x",
            mode="official", created_at="2026-11-07T10:00:00Z",
        ),
    )
    out = gateway.guarded(
        "case_read", {"case_id": world.case_x}, make_token(act="ghost", sid="sid-ghost")
    )
    assert out.allowed is False and out.event.reason == REVOKED


def test_revoking_mid_task_blocks_the_next_call_with_the_same_token(
    gateway, store, make_token, world
):
    token = make_token()
    assert gateway.guarded("case_read", {"case_id": world.case_x}, token).allowed is True
    store.revoke_sessions_for("maya")
    out = gateway.guarded("case_read", {"case_id": world.case_x}, token)
    assert out.allowed is False and out.event.reason == REVOKED


# ---- policy deny ----------------------------------------------------------


def test_bulk_export_is_denied_with_the_forbid_policy_and_atlas_id(
    gateway, store, make_token, events_of
):
    out = gateway.guarded("records_bulk_export", {"query": "all constituents"}, make_token())
    assert out.allowed is False and out.result is None
    e = _only_event(events_of, store)
    assert (e.decision, e.reason) == ("DENY", DENIED)
    assert e.policy_ids == ["forbid-bulk-export"]
    assert "AML.T0053" in e.atlas


def test_bulk_export_stays_denied_even_if_the_authorizer_would_allow(
    store, secret, make_token, handler_spy
):
    g = Gateway(store, AllowEverything(), secret)
    out = g.guarded("records_bulk_export", {"query": "all"}, make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []


def test_every_deny_event_carries_the_atlas_prompt_injection_id(gateway, store, make_token, world):
    gateway.guarded("case_read", {"case_id": world.case_y}, make_token())
    store.revoke_sessions_for("maya")
    gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    with pytest.raises(InvalidToken):
        gateway.guarded("case_read", {"case_id": world.case_x}, "garbage")
    denies = [e for e in store.events_since(None)[0] if e.decision == "DENY"]
    assert len(denies) == 3
    assert all("AML.T0053" in e.atlas for e in denies)


def test_deny_text_names_reason_and_atlas_ids(gateway, make_token):
    out = gateway.guarded("records_bulk_export", {"query": "all"}, make_token())
    assert out.deny_text() == (
        f"DENIED ({out.event.reason}, ATLAS {', '.join(out.event.atlas)}) policy: {', '.join(out.event.policy_ids)}"
    )
    assert out.deny_text().startswith("DENIED (denied by policy, ATLAS AML.T0053")


# ---- token failures -------------------------------------------------------


def test_bad_token_writes_a_deny_event_then_raises(gateway, store, events_of):
    with pytest.raises(InvalidToken):
        gateway.guarded("case_read", {"case_id": "case-1"}, "not-a-token")
    e = _only_event(events_of, store)
    assert (e.decision, e.reason, e.tool) == ("DENY", "invalid token", "case_read")


def test_expired_token_raises_and_runs_nothing(gateway, make_token, handler_spy, authorizer):
    now = int(time.time())
    token = make_token(iat=now - 2000, exp=now - 1000)
    with pytest.raises(InvalidToken):
        gateway.guarded("email_send", KNOWN_ARGS["email_send"], token)
    assert handler_spy.calls == [] and authorizer.requests == []


# ---- anything that goes wrong is a DENY ----------------------------------


def test_store_error_is_a_deny_not_an_exception(gateway, store, make_token, monkeypatch, world, handler_spy):
    def boom(_sid):
        raise ConnectionError("dynamodb down")

    monkeypatch.setattr(store, "get_session", boom)
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    assert out.allowed is False and out.result is None
    assert out.event.decision == "DENY"
    assert handler_spy.calls == []


def test_authorizer_error_is_a_deny(store, secret, make_token, world, events_of, handler_spy):
    g = Gateway(store, Exploding(), secret)
    out = g.guarded("email_send", KNOWN_ARGS["email_send"], make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []
    e = _only_event(events_of, store)
    assert e.decision == "DENY"


def test_tool_handler_error_is_a_deny(trusted_replies, gateway, make_token, handler_spy):
    handler_spy.boom = True
    out = gateway.guarded("email_send", KNOWN_ARGS["email_send"], make_token())
    assert handler_spy.calls, "the handler was supposed to run and fail"
    assert out.allowed is False and out.result is None
    assert out.event.decision == "DENY"


def test_scan_error_is_a_deny(gateway, scan_spy, make_token, world):
    scan_spy.error = RuntimeError("guardrail down")
    out = gateway.guarded("inbox_read", {"email_id": world.email_id}, make_token())
    assert scan_spy.calls
    assert out.allowed is False and out.result is None


def test_unknown_tool_is_a_deny(gateway, store, make_token, events_of):
    out = gateway.guarded("drop_all_tables", {}, make_token())
    assert out.allowed is False and out.result is None
    assert _only_event(events_of, store).tool == "drop_all_tables"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("case_read", {}),
        ("case_read", {"case_id": 12345}),
        ("email_send", {"to": "jane.doe@example.com"}),
        ("social_post", {"text": "no account"}),
        ("inbox_read", {"email_id": None}),
    ],
)
def test_invalid_arguments_are_a_deny(gateway, make_token, handler_spy, tool, args):
    out = gateway.guarded(tool, args, make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []


def test_unknown_case_is_a_deny(gateway, make_token):
    out = gateway.guarded("case_read", {"case_id": "case-404"}, make_token())
    assert out.allowed is False and out.result is None


def test_an_unaudited_call_is_never_allowed(gateway, store, make_token, monkeypatch, world):
    def boom(_event):
        raise ConnectionError("cannot write audit log")

    monkeypatch.setattr(store, "put_event", boom)
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    assert out.allowed is False and out.result is None


# ---- guardrail scan -------------------------------------------------------


def test_scan_runs_only_for_untrusted_output_tools(gateway, scan_spy, make_token, world):
    untrusted = {name for name, spec in TOOLS.items() if spec.untrusted_output}
    assert untrusted == {"inbox_read", "web_scrape"}
    for tool, args in KNOWN_ARGS.items():
        scan_spy.calls.clear()
        gateway.guarded(tool, args, make_token())
        assert bool(scan_spy.calls) == (tool in untrusted), tool


def test_scan_receives_the_untrusted_text(gateway, scan_spy, make_token, world):
    gateway.guarded("inbox_read", {"email_id": world.email_id}, make_token())
    (text,) = scan_spy.calls
    assert world.email_body in text


def test_flagged_output_is_recorded_but_passed_on_unchanged(
    store, authorizer, secret, make_token, world, events_of
):
    clean = Gateway(store, authorizer, secret).guarded(
        "inbox_read", {"email_id": world.email_id}, make_token()
    )
    flag = GuardrailFlag(flagged=True, categories=["PROMPT_ATTACK"])
    g = Gateway(store, authorizer, secret, scan=lambda text: flag)
    out = g.guarded("inbox_read", {"email_id": world.email_id}, make_token())
    assert out.allowed is True
    assert out.result == clean.result
    assert out.event.guardrail == flag
    assert events_of(store)[-1].guardrail == flag


def test_unflagged_event_has_a_clean_guardrail_field(gateway, make_token, world):
    out = gateway.guarded("inbox_read", {"email_id": world.email_id}, make_token())
    assert out.event.guardrail.flagged is False


def test_denied_call_is_not_scanned(gateway, store, scan_spy, make_token, world):
    store.revoke_sessions_for("maya")
    gateway.guarded("inbox_read", {"email_id": world.email_id}, make_token())
    assert scan_spy.calls == []


# ---- process-wide instance ------------------------------------------------


def test_set_gateway_replaces_the_process_wide_instance(gateway):
    set_gateway(gateway)
    assert get_gateway() is gateway


def test_deny_text_names_the_cedar_policy(gateway, make_token):
    out = gateway.guarded("records_bulk_export", {"query": "all constituent cases rep-x"}, make_token())
    assert not out.allowed and "policy: forbid-bulk-export" in out.deny_text()
