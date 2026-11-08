"""Authorizers: the AVP wrapper (fake client) and the starter-policy test double."""

from __future__ import annotations

import pytest
from identity_gateway.authz import (
    AuthzDecision,
    AuthzRequest,
    AvpAuthorizer,
    StarterPolicyAuthorizer,
)
from mandate_contracts import cedar

STORE_ID = "ps-123"
NAMES = {"avp-1": "case-read-same-office", "avp-2": "forbid-bulk-export"}


def _req(tool, *, office="rep-x", mode="official", role="chief_of_staff", resource=None, agent="agent-1", **ctx):
    return AuthzRequest(
        agent_id=agent,
        office=office,
        mode=mode,
        role=role,
        tool=tool,
        resource=resource or cedar.tool_entity(tool),
        context=cedar.build_context(**ctx),
    )


class FakeAvp:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def is_authorized(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _resp(decision, policies=(), errors=()):
    return {
        "decision": decision,
        "determiningPolicies": [{"policyId": p} for p in policies],
        "errors": [{"errorDescription": e} for e in errors],
    }


# ---- AvpAuthorizer --------------------------------------------------------


def test_avp_request_is_built_from_the_contract_helpers():
    client = FakeAvp(_resp("ALLOW", ["avp-1"]))
    case = cedar.case_entity("case-1", "rep-x")
    req = _req("case_read", resource=case, delegatorActive=True)

    AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(req)

    (call,) = client.calls
    assert call["policyStoreId"] == STORE_ID
    assert call["principal"] == {"entityType": "Mandate::Agent", "entityId": "agent-1"}
    assert call["action"] == cedar.action("case_read")
    assert call["resource"] == case["identifier"]
    assert call["context"] == req.context
    assert call["entities"] == {"entityList": [cedar.agent_entity("agent-1", "rep-x", "official", "chief_of_staff"), case]}


def test_avp_allow_maps_policy_ids_to_readable_names():
    client = FakeAvp(_resp("ALLOW", ["avp-1"]))
    d = AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(_req("case_read", delegatorActive=True))
    assert isinstance(d, AuthzDecision)
    assert d.allowed is True
    assert d.policy_ids == ["case-read-same-office"]


def test_avp_deny_is_not_allowed_and_still_reports_the_forbid_policy():
    client = FakeAvp(_resp("DENY", ["avp-2"]))
    d = AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(_req("records_bulk_export"))
    assert d.allowed is False
    assert d.policy_ids == ["forbid-bulk-export"]


def test_avp_deny_with_no_determining_policy_has_empty_policy_ids():
    client = FakeAvp(_resp("DENY"))
    d = AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(_req("case_read"))
    assert d.allowed is False and d.policy_ids == []


def test_avp_unknown_policy_id_is_passed_through_raw():
    client = FakeAvp(_resp("ALLOW", ["avp-1", "avp-mystery"]))
    d = AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(_req("case_read", delegatorActive=True))
    assert d.policy_ids == ["case-read-same-office", "avp-mystery"]


def test_avp_allow_with_evaluation_errors_is_not_allowed():
    client = FakeAvp(_resp("ALLOW", ["avp-1"], errors=["attribute missing"]))
    d = AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(_req("case_read", delegatorActive=True))
    assert d.allowed is False


@pytest.mark.parametrize("decision", ["allow", "Allow", "", None, "PERMIT"])
def test_avp_only_the_exact_word_allow_allows(decision):
    client = FakeAvp(_resp(decision, ["avp-1"]))
    d = AvpAuthorizer(client, STORE_ID, NAMES).is_authorized(_req("case_read", delegatorActive=True))
    assert d.allowed is False


def test_avp_client_error_propagates_so_guarded_can_deny():
    boom = ConnectionError("AVP unreachable")
    authorizer = AvpAuthorizer(FakeAvp(error=boom), STORE_ID, NAMES)
    with pytest.raises(ConnectionError):
        authorizer.is_authorized(_req("case_read"))


# ---- StarterPolicyAuthorizer ---------------------------------------------


def _decide(tool, **kw):
    return StarterPolicyAuthorizer().is_authorized(_req(tool, **kw))


def test_starter_case_read_same_office_is_allowed():
    case = cedar.case_entity("case-1", "rep-x")
    d = _decide("case_read", resource=case, delegatorActive=True)
    assert d.allowed is True and d.policy_ids == ["case-read-same-office"]


def test_starter_case_read_other_office_has_no_matching_permit():
    case = cedar.case_entity("case-2", "rep-y")
    d = _decide("case_read", resource=case, delegatorActive=True)
    assert d.allowed is False and d.policy_ids == []


def test_starter_case_read_with_inactive_delegator_is_denied():
    case = cedar.case_entity("case-1", "rep-x")
    d = _decide("case_read", resource=case, delegatorActive=False)
    assert d.allowed is False


@pytest.mark.parametrize("tool", ["inbox_read", "datagov_search", "web_scrape"])
def test_starter_read_tools_need_an_active_delegator(tool):
    ok = _decide(tool, delegatorActive=True)
    assert ok.allowed is True and ok.policy_ids == ["read-tools"]
    assert _decide(tool, delegatorActive=False).allowed is False


def test_starter_email_to_known_recipient_is_allowed():
    d = _decide("email_send", delegatorActive=True, recipientKnown=True)
    assert d.allowed is True and d.policy_ids == ["email-send-known-recipient"]


def test_starter_email_to_unknown_recipient_is_denied():
    d = _decide("email_send", delegatorActive=True, recipientKnown=False)
    assert d.allowed is False and d.policy_ids == []


def test_starter_official_post_is_allowed():
    d = _decide("social_post", delegatorActive=True, account="official")
    assert d.allowed is True and d.policy_ids == ["social-post-official"]


def test_starter_campaign_post_in_official_mode_hits_the_forbid():
    d = _decide("social_post", delegatorActive=True, account="campaign")
    assert d.allowed is False and d.policy_ids == ["forbid-campaign-post"]


def test_starter_bulk_export_is_forbidden_even_for_an_active_delegator():
    d = _decide("records_bulk_export", delegatorActive=True, recipientKnown=True, account="official")
    assert d.allowed is False and d.policy_ids == ["forbid-bulk-export"]
