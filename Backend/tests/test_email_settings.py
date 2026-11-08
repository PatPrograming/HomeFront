"""Per-agent email settings: sender, display name, reply-to and signature."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import ADMIN

from identity_gateway import agents_api as gateway  # noqa: E402
from identity_gateway.dynamo_repo import agent_from_item, agent_to_item  # noqa: E402
from identity_gateway.runner import system_prompt  # noqa: E402
from identity_gateway.store import EmailSettings  # noqa: E402
from mcp_tools import handlers  # noqa: E402

# Every call signs in as an admin with our Cognito login (tests/helpers.py).
client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login")
TO = "ana@example.gov"
SETTINGS = {
    "from_email": "Notices@City.gov",
    "from_name": "City of Springfield",
    "reply_to": "help@city.gov",
    "signature": "City of Springfield\r\nOffice of Communications",
}


def _agent(recipients=(TO,)):
    body = {"agent_id": f"agent-es-{uuid.uuid4().hex[:8]}", "name": "Comms", "agency": "City", "kind": "email"}
    r = client.post("/api/v1/agents", json={**body, "recipients": list(recipients)})
    assert r.status_code == 201, r.text
    return r.json()


def _put(agent_id, settings):
    return client.put(f"/api/v1/agents/{agent_id}/email-settings", json=settings)


def _approve(agent_id, tool, args):
    r = client.post("/api/v1/auth/token", json={"agent_id": agent_id})
    headers = {"X-Agent-Token": r.json()['access_token']}
    out = client.post("/api/v1/agents/tool-call", json={"tool": tool, "arguments": args}, headers=headers).json()
    assert out["decision"] == "pending_approval", out
    view = client.get(f"/api/v1/approvals/{out['approval_id']}").json()
    done = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={}).json()
    return view, done


@pytest.fixture
def ses(monkeypatch):
    """Capture SES sends instead of calling AWS."""
    sent = []
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: None)
    monkeypatch.setattr(
        handlers, "_ses_send", lambda c, sender, to, subj, body: sent.append((sender, to, subj, body)) or "m-1"
    )
    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    return sent


# --- managing settings ---------------------------------------------------------------


def test_new_agent_has_empty_settings():
    assert _agent()["email_settings"] == {"from_email": "", "from_name": "", "reply_to": "", "signature": ""}


def test_settings_are_normalized_and_audited():
    agent = _agent()
    r = _put(agent["agent_id"], {**SETTINGS, "from_name": "  City of\n Springfield "})
    assert r.status_code == 200, r.text
    assert r.json()["email_settings"] == {
        "from_email": "notices@city.gov",
        "from_name": "City of Springfield",  # one line: no header injection
        "reply_to": "help@city.gov",
        "signature": "City of Springfield\nOffice of Communications",
    }
    events = client.get(
        "/api/v1/audit", params={"agent_id": agent["agent_id"], "type": "email_settings_updated"}
    ).json()
    assert events and events[0]["fields"] == ["from_email", "from_name", "reply_to", "signature"]


@pytest.mark.parametrize(
    "bad, field",
    [
        ({"from_email": "not-an-email"}, "from_email"),
        ({"reply_to": "a@b"}, "reply_to"),
        ({"from_name": "x" * 81}, "from_name"),
        ({"signature": "x" * 1001}, "signature"),
    ],
)
def test_invalid_settings_are_rejected(bad, field):
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    r = _put(agent["agent_id"], {**SETTINGS, **bad})
    assert r.status_code == 400
    assert r.json()["detail"]["field"] == field
    stored = client.get(f"/api/v1/agents/{agent['agent_id']}").json()["email_settings"]
    assert stored["from_email"] == "notices@city.gov"  # unchanged


def test_settings_round_trip_through_dynamo_item():
    agent = gateway.store.get_agent(_agent()["agent_id"])
    agent.email_settings = EmailSettings(**{**SETTINGS, "signature": "Thanks"})
    assert agent_from_item(agent_to_item(agent)).email_settings == agent.email_settings


def test_legacy_dynamo_item_without_settings_loads():
    item = agent_to_item(gateway.store.get_agent(_agent()["agent_id"]))
    item.pop("email_settings")
    assert agent_from_item(item).email_settings == EmailSettings()


def test_returned_records_do_not_share_settings():
    aid = _agent()["agent_id"]
    got = gateway.store.get_agent(aid)
    got.email_settings.from_email = "mutated@city.gov"
    assert gateway.store.get_agent(aid).email_settings.from_email == ""


# --- sending -------------------------------------------------------------------------


def test_send_uses_agent_sender_reply_to_and_signature(ses):
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    view, done = _approve(agent["agent_id"], "email.send", {"to": TO, "subject": "Hi", "body": "Town hall Tuesday.\n"})

    assert view["sender"] == {
        "from": "City of Springfield <notices@city.gov>",
        "from_source": "agent",
        "reply_to": "help@city.gov",
        "signature": "City of Springfield\nOffice of Communications",
        "simulated": False,
    }
    assert done["status"] == "executed"
    sender, to, subject, body = ses[0]
    assert sender["from_header"] == "City of Springfield <notices@city.gov>"
    assert sender["reply_to"] == "help@city.gov"
    assert body == "Town hall Tuesday.\n\nCity of Springfield\nOffice of Communications"


def test_list_send_appends_signature_to_every_copy(ses):
    agent = _agent([TO, "ben@example.gov"])
    _put(agent["agent_id"], {"from_email": "notices@city.gov", "signature": "-- City"})
    _, done = _approve(agent["agent_id"], "email.send_list", {"subject": "s", "body": "Hello"})
    assert done["result"]["sent"] == 2
    assert [b for *_, b in ses] == ["Hello\n\n-- City", "Hello\n\n-- City"]


def test_non_ascii_sender_name_is_encoded(ses):
    agent = _agent()
    _put(agent["agent_id"], {"from_email": "notices@city.gov", "from_name": "Ciudad de Peñalosa"})
    _approve(agent["agent_id"], "email.send", {"to": TO, "subject": "s", "body": "b"})
    header = ses[0][0]["from_header"]
    assert header.startswith("=?utf-8?") and header.endswith("<notices@city.gov>")


def test_reviewer_edits_cannot_change_sender(ses):
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    r = client.post("/api/v1/auth/token", json={"agent_id": agent["agent_id"]})
    headers = {"X-Agent-Token": r.json()['access_token']}
    out = client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "email.send", "arguments": {"to": TO, "subject": "s", "body": "b"}},
        headers=headers,
    ).json()
    edited = {"to": TO, "subject": "s", "body": "b", "sender": {"from_email": "evil@attacker.example"}}
    r = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={"arguments": edited})
    assert r.status_code == 400  # extra fields are rejected outright
    assert ses == []


def test_injected_sender_overrides_agent_arguments(ses, monkeypatch):
    """Even if a `sender` argument got past schema checks, the agent's record wins."""
    monkeypatch.setattr(gateway, "_validate_arguments", lambda tool, args: None)
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    rec = gateway.store.approvals.insert(
        gateway.ApprovalRecord(
            agent_id=agent["agent_id"],
            tool="email.send",
            arguments={"to": TO, "subject": "s", "body": "b", "sender": {"from_email": "evil@attacker.example"}},
        )
    )
    done = client.post(f"/api/v1/approvals/{rec.approval_id}/approve", json={}).json()
    assert done["status"] == "executed"
    assert ses[0][0]["from_email"] == "notices@city.gov"


def test_env_sender_is_the_fallback(ses, monkeypatch):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "default@city.gov")
    agent = _agent()
    view, done = _approve(agent["agent_id"], "email.send", {"to": TO, "subject": "s", "body": "b"})
    assert view["sender"]["from"] == "default@city.gov" and view["sender"]["from_source"] == "env"
    assert done["result"]["status"] == "sent" and ses[0][0]["from_header"] == "default@city.gov"


def test_no_sender_anywhere_simulates(ses):
    agent = _agent()
    view, done = _approve(agent["agent_id"], "email.send", {"to": TO, "subject": "s", "body": "b"})
    assert view["sender"]["simulated"] is True
    assert done["result"]["status"] == "simulated" and ses == []


# --- verification --------------------------------------------------------------------


def test_verification_without_credentials_is_null(monkeypatch):
    from botocore.exceptions import NoCredentialsError

    def no_creds(quick=False):
        raise NoCredentialsError()

    monkeypatch.setattr(handlers, "_ses_client", no_creds)
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    r = client.get(f"/api/v1/agents/{agent['agent_id']}/email-settings/verification")
    assert r.status_code == 200
    assert r.json()["verified"] is None and "NoCredentialsError" in r.json()["reason"]


def test_verification_checks_address_then_domain(monkeypatch):
    from botocore.exceptions import ClientError

    class FakeSes:
        def get_email_identity(self, EmailIdentity):
            if EmailIdentity == "city.gov":
                return {"VerifiedForSendingStatus": True}
            raise ClientError({"Error": {"Code": "NotFoundException"}}, "GetEmailIdentity")

    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: FakeSes())
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    out = client.get(f"/api/v1/agents/{agent['agent_id']}/email-settings/verification").json()
    assert out["verified"] is True and out["identity"] == "city.gov"


def test_verification_with_no_sender(monkeypatch):
    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    out = client.get(f"/api/v1/agents/{_agent()['agent_id']}/email-settings/verification").json()
    assert out["verified"] is None and out["from_email"] is None


# --- runner --------------------------------------------------------------------------


def test_prompt_mentions_sender_name_and_signature():
    agent = _agent()
    _put(agent["agent_id"], SETTINGS)
    prompt = system_prompt(gateway.store.get_agent(agent["agent_id"]))
    assert '"City of Springfield"' in prompt and "signature is added" in prompt
