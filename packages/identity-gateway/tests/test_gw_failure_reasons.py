"""A server-side failure is still a fail-closed DENY, but its reason names the real cause.

Before: any exception inside guarded() was recorded as "denied by policy", so an expired AWS
credential looked exactly like a Cedar forbid. No AWS: the failures are raised by fakes.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from identity_gateway.gateway import Gateway
from mandate_contracts import GuardrailFlag

from test_gw_guarded import KNOWN_ARGS, _only_event


def client_error(code, operation="IsAuthorized"):
    return ClientError({"Error": {"Code": code, "Message": "AKIA-SECRET-LOOKING detail"}}, operation)


class Raising:
    def __init__(self, error):
        self.error = error

    def is_authorized(self, req):
        raise self.error


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (NoCredentialsError(), "AWS credentials expired on the server"),
        (client_error("ExpiredTokenException"), "AWS credentials expired on the server"),
        (client_error("UnrecognizedClientException"), "AWS credentials expired on the server"),
        (client_error("AccessDeniedException"), "server lacks AWS permission"),
        (client_error("ThrottlingException"), "permission service unavailable"),
        (EndpointConnectionError(endpoint_url="https://verifiedpermissions.us-east-1.amazonaws.com"),
         "permission service unavailable"),
        (TimeoutError("AVP timed out"), "permission service unavailable"),
        (RuntimeError("something odd"), "server error (failed closed)"),
    ],
)
def test_a_permission_check_failure_names_its_cause(store, secret, make_token, events_of, handler_spy, error, reason):
    g = Gateway(store, Raising(error), secret)
    out = g.guarded("email_send", KNOWN_ARGS["email_send"], make_token())

    assert out.allowed is False and out.result is None and handler_spy.calls == []  # still fail closed
    e = _only_event(events_of, store)
    assert (e.decision, e.reason, e.policy_ids) == ("DENY", reason, [])
    assert e.atlas == g._atlas("email_send", "DENY", GuardrailFlag()), "same ATLAS labels as any DENY"
    assert reason in out.deny_text() and "AKIA" not in out.deny_text()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ConnectionError("dynamodb down"), "server error (failed closed)"),
        (NoCredentialsError(), "AWS credentials expired on the server"),
        (client_error("AccessDeniedException", "GetItem"), "server lacks AWS permission"),
    ],
)
def test_a_store_failure_names_its_cause_but_never_blames_the_permission_service(
    gateway, store, make_token, monkeypatch, world, events_of, error, reason
):
    def boom(_sid):
        raise error

    monkeypatch.setattr(store, "get_session", boom)
    out = gateway.guarded("case_read", {"case_id": world.case_x}, make_token())
    assert out.allowed is False
    assert out.event.reason == reason


def test_a_real_cedar_denial_still_says_denied_by_policy(gateway, make_token):
    out = gateway.guarded("records_bulk_export", {"query": "everything"}, make_token())
    assert out.event.reason == "denied by policy"
