"""Agency logo upload and the branded email template (HTML + text, logo inline)."""
import base64
import email
import struct
import uuid
import zlib
from email import policy

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.main import app
from tests.helpers import ADMIN

from identity_gateway import agents_api as gateway  # noqa: E402
from identity_gateway import logo_store  # noqa: E402
from identity_gateway.dynamo_repo import agent_from_item, agent_to_item  # noqa: E402
from mcp_tools import email_template, handlers  # noqa: E402

client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login")
TO = "ana@example.gov"
SENDER = "notices@city.gov"


def png(width=120, height=60) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg(width=300, height=100) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FakeSes:
    def __init__(self):
        self.sent = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "ses-1"}


@pytest.fixture
def ses(monkeypatch):
    fake = FakeSes()
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: fake)
    return fake


@pytest.fixture(autouse=True)
def fresh_logo_store():
    gateway.store.use_logo_repository(logo_store.MemoryLogoRepository())
    yield


def _agent(sender=SENDER):
    body = {
        "agent_id": f"agent-br-{uuid.uuid4().hex[:8]}",
        "name": "Comms",
        "agency": "City of Springfield",
        "kind": "email",
        "recipients": [TO],
    }
    r = client.post("/api/v1/agents", json=body)
    assert r.status_code == 201, r.text
    agent = r.json()
    r = client.put(
        f"/api/v1/agents/{agent['agent_id']}/email-settings",
        json={"from_email": sender, "reply_to": "help@city.gov", "signature": "Office of Communications"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _upload(agent_id, data, filename="seal.png"):
    return client.put(f"/api/v1/agents/{agent_id}/email-logo", json={"data_base64": b64(data), "filename": filename})


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=policy.default)


# --- validation ----------------------------------------------------------------------


def test_sniff_reads_dimensions():
    assert logo_store.sniff(png(120, 60)) == ("image/png", (120, 60))
    assert logo_store.sniff(jpeg(300, 100)) == ("image/jpeg", (300, 100))
    assert logo_store.sniff(b"GIF89a" + struct.pack("<HH", 40, 20) + b"\x00" * 8) == ("image/gif", (40, 20))


@pytest.mark.parametrize(
    "data, message",
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>", "PNG, JPEG or GIF"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (logo_store.MAX_LOGO_BYTES), "limited to 512 KB"),
        (png(5000, 10), "4000"),
    ],
)
def test_bad_uploads_are_400(data, message):
    agent = _agent()
    r = _upload(agent["agent_id"], data)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_logo" and message in r.json()["detail"]["message"]


def test_not_base64_is_400():
    agent = _agent()
    r = client.put(f"/api/v1/agents/{agent['agent_id']}/email-logo", json={"data_base64": "not base64!!"})
    assert r.status_code == 400


# --- upload / delete ---------------------------------------------------------------------


def test_upload_replace_and_delete():
    agent = _agent()
    r = _upload(agent["agent_id"], png())
    assert r.status_code == 200, r.text
    view = r.json()["email_logo"]
    assert view["content_type"] == "image/png" and (view["width"], view["height"]) == (120, 60)
    assert "key" not in view and "sha256" not in view
    first_key = gateway.store.get_agent(agent["agent_id"]).email_logo["key"]
    assert gateway.store.logos.get(first_key) == png()

    _upload(agent["agent_id"], jpeg(), "seal.jpg")
    second = gateway.store.get_agent(agent["agent_id"]).email_logo
    assert second["content_type"] == "image/jpeg"
    assert gateway.store.logos.get(first_key) is None  # the old object is cleaned up

    r = client.delete(f"/api/v1/agents/{agent['agent_id']}/email-logo")
    assert r.status_code == 200 and r.json()["email_logo"] is None
    assert gateway.store.logos.get(second["key"]) is None
    events = client.get(
        "/api/v1/audit", params={"agent_id": agent["agent_id"], "type": "email_logo_updated"}
    ).json()
    assert len(events) == 3


def test_storage_failure_is_502_and_keeps_old_logo(monkeypatch):
    agent = _agent()
    _upload(agent["agent_id"], png())

    def boom(*_a, **_k):
        raise RuntimeError("S3 down")

    monkeypatch.setattr(gateway.store.logos, "put", boom)
    r = _upload(agent["agent_id"], jpeg())
    assert r.status_code == 502 and r.json()["detail"]["error"] == "logo_storage_failed"
    assert gateway.store.get_agent(agent["agent_id"]).email_logo["content_type"] == "image/png"


def test_logo_metadata_round_trips_through_dynamodb_items():
    agent = _agent()
    _upload(agent["agent_id"], png())
    rec = gateway.store.get_agent(agent["agent_id"])
    item = agent_to_item(rec)
    assert not any(isinstance(v, float) for v in item["email_logo"].values())
    back = agent_from_item(item)
    assert back.email_logo["width"] == 120 and isinstance(back.email_logo["width"], int)
    assert back.email_logo["uploaded_at"] == pytest.approx(rec.email_logo["uploaded_at"])


@mock_aws
def test_s3_repository():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="logos")
    repo = logo_store.S3LogoRepository("logos", client=s3)
    repo.put("logos/a/x.png", png(), "image/png")
    head = s3.head_object(Bucket="logos", Key="logos/a/x.png")
    assert head["ContentType"] == "image/png" and head["ServerSideEncryption"] == "AES256"
    assert repo.get("logos/a/x.png") == png()
    repo.delete("logos/a/x.png")
    assert repo.get("logos/a/x.png") is None


# --- the template -------------------------------------------------------------------------


def test_template_escapes_and_links():
    out = email_template.render_html(
        "<b>Alert</b>",
        "Hi <script>alert(1)</script>\nSee https://example.gov/a?x=1&y=2.",
        "",
        {"agency": "City & County"},
    )
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "City &amp; County" in out and "&lt;b&gt;Alert&lt;/b&gt;" in out
    assert '<a href="https://example.gov/a?x=1&amp;y=2"' in out and "y=2</a>." in out


def test_logo_box_scales_down_never_up():
    assert email_template.logo_box(120, 60) == (120, 60)
    assert email_template.logo_box(400, 400) == (64, 64)
    assert email_template.logo_box(2000, 100) == (240, 12)


# --- what gets sent -------------------------------------------------------------------------


def test_approved_send_is_branded_multipart_with_inline_logo(ses):
    agent = _agent()
    _upload(agent["agent_id"], png())
    r = client.post("/api/v1/auth/token", json={"agent_id": agent["agent_id"]})
    headers = {"X-Agent-Token": r.json()["access_token"]}
    args = {"to": TO, "subject": "Water notice", "body": "Service resumes at 3 p.m."}
    out = client.post("/api/v1/agents/tool-call", json={"tool": "email.send", "arguments": args}, headers=headers).json()
    done = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={})
    assert done.status_code == 200, done.text

    [req] = ses.sent
    assert req["FromEmailAddress"] == SENDER and req["Destination"] == {"ToAddresses": [TO]}
    msg = _parse(req["Content"]["Raw"]["Data"])
    assert msg["Subject"] == "Water notice" and msg["Reply-To"] == "help@city.gov"
    assert msg["List-Unsubscribe"] is None  # one-to-one mail, not a list send
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "Service resumes at 3 p.m." in text and "Office of Communications" in text
    assert "official message from City of Springfield" in text
    assert "City of Springfield" in html and "Office of Communications" in html
    [image] = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert image.get_content() == png()
    cid = image["Content-ID"].strip("<>")
    assert f'src="cid:{cid}"' in html


def test_list_send_has_unsubscribe_header(ses):
    sender = handlers.resolve_sender({"from_email": SENDER, "reply_to": "help@city.gov", "agency": "City"})
    out = handlers.handle_email_send_list(
        {"recipients": [TO], "subject": "S", "body": "B", "sender": {**sender, "from_email": SENDER}}
    )
    assert out["sent"] == 1
    msg = _parse(ses.sent[0]["Content"]["Raw"]["Data"])
    assert msg["List-Unsubscribe"] == "<mailto:help@city.gov?subject=unsubscribe>"
    assert "mailing list" in msg.get_body(("plain",)).get_content()


def test_a_missing_logo_object_sends_without_it(ses):
    agent = _agent()
    _upload(agent["agent_id"], png())
    gateway.store.use_logo_repository(logo_store.MemoryLogoRepository())  # bytes gone
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/email-settings/test", json={"to": TO})
    assert r.json()["ok"] is True
    msg = _parse(ses.sent[0]["Content"]["Raw"]["Data"])
    assert not [p for p in msg.walk() if p.get_content_maintype() == "image"]
    assert "cid:" not in msg.get_body(("html",)).get_content()


def test_preview_inlines_logo_as_data_url():
    agent = _agent()
    _upload(agent["agent_id"], png())
    out = client.get(f"/api/v1/agents/{agent['agent_id']}/email-preview").json()
    assert out["logo_data_url"].startswith("data:image/png;base64,")
    assert out["logo_data_url"] in out["html"] and "cid:" not in out["html"]
    assert "City of Springfield" in out["html"] and out["from"] == SENDER
