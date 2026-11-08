"""One test per row of the manager brief section 10 (edge cases), plus the offboarding sequence."""

from __future__ import annotations

import time

import pytest
from identity_gateway.authz import AuthzDecision
from identity_gateway.gateway import Gateway
from identity_gateway.storage import MandateStore
from identity_gateway.tokens import InvalidToken

REVOKED = "session revoked / delegator inactive"
DENIED = "denied by policy"


def _events(store):
    return store.events_since(None, limit=1000)[0]


def test_case_from_another_office_is_denied(gateway, store, make_token, world, handler_spy):
    out = gateway.guarded("case_read", {"case_id": world.case_y}, make_token())
    assert out.allowed is False
    assert out.result is None
    assert out.event.reason == DENIED
    assert [e.decision for e in _events(store)] == ["DENY"]


def test_agent_supplied_office_is_ignored(gateway, authorizer, make_token, world):
    # Lying about the office in the arguments must change nothing in either direction.
    deny = gateway.guarded("case_read", {"case_id": world.case_y, "office": "rep-x"}, make_token())
    allow = gateway.guarded("case_read", {"case_id": world.case_x, "office": "rep-y"}, make_token())
    assert deny.allowed is False
    assert allow.allowed is True
    offices = [(r.office, r.resource["attributes"]["office"]) for r in authorizer.requests]
    assert offices == [("rep-x", {"string": "rep-y"}), ("rep-x", {"string": "rep-x"})]


def test_email_to_an_unknown_address_is_denied(gateway, make_token, handler_spy):
    out = gateway.guarded(
        "email_send", {"to": "reporter@news.example.com", "subject": "s", "body": "b"}, make_token()
    )
    assert out.allowed is False and out.result is None
    assert out.event.reason == DENIED
    assert handler_spy.calls == []


def test_social_post_to_the_campaign_account_is_denied(maya_posts, gateway, make_token, handler_spy):
    out = gateway.guarded("social_post", {"account": "campaign", "text": "Vote!"}, make_token())
    assert out.allowed is False and out.result is None
    assert out.event.policy_ids == ["forbid-campaign-post"]
    assert handler_spy.calls == []


def test_old_token_after_offboarding_is_denied(gateway, store, make_token, world, handler_spy):
    token = make_token()
    assert gateway.guarded("case_read", {"case_id": world.case_x}, token).allowed is True

    store.set_staffer_active("maya", False)
    store.revoke_sessions_for("maya")

    out = gateway.guarded("email_send", {"to": world.known_email, "subject": "s", "body": "b"}, token)
    assert out.allowed is False and out.result is None
    assert out.event.reason == REVOKED
    assert handler_spy.calls == []


def test_expired_token_is_rejected_for_the_401_path(gateway, store, make_token):
    now = int(time.time())
    token = make_token(iat=now - 2000, exp=now - 1000)
    with pytest.raises(InvalidToken):
        gateway.guarded("case_read", {"case_id": "case-1"}, token)
    (e,) = _events(store)
    assert (e.decision, e.reason) == ("DENY", "invalid token")


def test_tampered_token_is_rejected(gateway, store, make_token):
    head, body, sig = make_token().split(".")
    flipped = "B" if body[10] != "B" else "C"  # a middle character, so real payload bits change
    tampered = ".".join([head, body[:10] + flipped + body[11:], sig])
    with pytest.raises(InvalidToken):
        gateway.guarded("case_read", {"case_id": "case-1"}, tampered)
    (e,) = _events(store)
    assert e.reason == "invalid token"


def test_token_signed_with_a_different_secret_is_rejected(gateway, make_token):
    forged = make_token(signing_secret="attacker-secret-attacker-secret-0000")
    with pytest.raises(InvalidToken):
        gateway.guarded("case_read", {"case_id": "case-1"}, forged)


def test_missing_token_is_rejected(gateway, store, handler_spy):
    with pytest.raises(InvalidToken):
        gateway.guarded("email_send", {"to": "a@b.gov", "subject": "s", "body": "b"}, "")
    (e,) = _events(store)
    assert (e.decision, e.reason) == ("DENY", "invalid token")
    assert handler_spy.calls == []


def test_verified_permissions_unreachable_is_denied(store, secret, make_token, world, handler_spy):
    class Unreachable:
        def is_authorized(self, req):
            raise ConnectionError("cannot reach verifiedpermissions.us-east-1.amazonaws.com")

    g = Gateway(store, Unreachable(), secret)
    out = g.guarded("email_send", {"to": world.known_email, "subject": "s", "body": "b"}, make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []
    assert [e.decision for e in _events(store)] == ["DENY"]


def test_dynamodb_unreachable_is_denied(gateway, store, make_token, monkeypatch, world, handler_spy):
    def unreachable(_sid):
        raise ConnectionError("cannot reach dynamodb.us-east-1.amazonaws.com")

    monkeypatch.setattr(store, "get_session", unreachable)
    out = gateway.guarded("email_send", {"to": world.known_email, "subject": "s", "body": "b"}, make_token())
    assert out.allowed is False and out.result is None
    assert handler_spy.calls == []
    assert [e.decision for e in _events(store)] == ["DENY"]


def test_server_restart_keeps_revocations_and_the_audit_log(
    gateway, store, table, secret, make_token, world
):
    token = make_token()
    gateway.guarded("case_read", {"case_id": world.case_x}, token)  # ALLOW event
    store.set_staffer_active("maya", False)
    assert store.revoke_sessions_for("maya") == 1
    gateway.guarded("case_read", {"case_id": world.case_x}, token)  # DENY event
    before = [e.id for e in _events(store)]
    assert len(before) == 2

    # "Restart": brand-new store and gateway objects over the same table.
    from identity_gateway.authz import StarterPolicyAuthorizer

    store2 = MandateStore(table)
    gateway2 = Gateway(store2, StarterPolicyAuthorizer(), secret)

    assert store2.get_staffer("maya").active is False
    assert store2.get_session(world.sid).status == "revoked"
    assert [e.id for e in _events(store2)] == before
    out = gateway2.guarded("case_read", {"case_id": world.case_x}, token)
    assert out.allowed is False and out.event.reason == REVOKED


def test_offboarding_blocks_the_very_next_call(gateway, store, make_token, world):
    token = make_token()
    first = gateway.guarded("case_read", {"case_id": world.case_x}, token)
    assert first.allowed is True

    store.set_staffer_active("maya", False)
    n = store.revoke_sessions_for("maya")
    assert n == 1

    nxt = gateway.guarded("case_read", {"case_id": world.case_x}, token)
    assert nxt.allowed is False
    assert nxt.event.reason == REVOKED
    assert [e.decision for e in _events(store)] == ["ALLOW", "DENY"]


def test_deactivating_the_staffer_alone_also_blocks_calls(gateway, store, make_token, world):
    # Defence in depth: even if session revocation failed, the inactive delegator denies.
    store.set_staffer_active("maya", False)
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    assert out.allowed is False and out.event.reason == REVOKED


def test_bulk_export_is_denied_even_when_everything_else_is_valid(gateway, make_token):
    out = gateway.guarded("records_bulk_export", {"query": "everything"}, make_token())
    assert out.allowed is False
    assert out.event.policy_ids == ["forbid-bulk-export"]
    assert "AML.T0053" in out.event.atlas


def test_permissive_authorizer_cannot_leak_bulk_export(store, secret, make_token):
    class Permissive:
        def is_authorized(self, req):
            return AuthzDecision(allowed=True, policy_ids=["allow-all"])

    out = Gateway(store, Permissive(), secret).guarded(
        "records_bulk_export", {"query": "everything"}, make_token()
    )
    assert out.allowed is False
