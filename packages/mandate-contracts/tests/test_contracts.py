import json
import re
import sys
import time
from pathlib import Path

import jwt
import pytest

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG / "src"))

from typing import get_args  # noqa: E402

from mandate_contracts import TOOLS, DecisionEvent, MandateClaims, Reason, tool_schemas  # noqa: E402
from mandate_contracts.cedar import (  # noqa: E402
    STARTER_POLICIES,
    agent_entity,
    build_context,
    case_entity,
)
from mandate_contracts.ddb import (  # noqa: E402
    EmailItem,
    SessionItem,
    case_key,
    email_key,
    event_key,
    session_key,
    staffer_key,
)
from mandate_contracts.dev import dev_claims, mint_dev_token, verify_dev_token  # noqa: E402
from mandate_contracts.tools import CaseReadInput  # noqa: E402

SECRET = "test-only-secret-at-least-32-bytes-long"
BEDROCK_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def test_tool_list_matches_brief():
    research = [
        "census_query",
        "openfda_search",
        "eia_query",
        "nasa_search",
        "noaa_climate",
        "nps_parks",
        "bls_series",
        "fec_search",
        "epa_search",
        "fcc_search",
    ]
    assert list(TOOLS) == [
        "inbox_read",
        "case_read",
        "datagov_search",
        "web_scrape",
        "email_send",
        "social_post",
        "email_send_list",
        "social_reply",
        "usaspending_search",
        "sam_entity_lookup",
        "report_save",
        *research,
        "records_bulk_export",
    ]
    assert {n for n, t in TOOLS.items() if t.untrusted_output} == {
        "inbox_read",
        "web_scrape",
        "usaspending_search",
        "sam_entity_lookup",
        *research,
    }
    assert [n for n, t in TOOLS.items() if t.always_denied] == ["records_bulk_export"]


def test_tool_names_are_valid_bedrock_names():
    assert all(BEDROCK_TOOL_NAME.match(name) for name in TOOLS)


def test_committed_schemas_are_up_to_date():
    committed = json.loads((PKG / "schemas" / "tools.json").read_text())
    assert committed == tool_schemas(), "run packages/mandate-contracts/export_schemas.py"


def test_agent_supplied_office_is_dropped():
    parsed = CaseReadInput.model_validate({"case_id": "case-7", "office": "rep-y"})
    assert parsed.model_dump() == {"case_id": "case-7"}


def test_ts_types_match_python():
    ts = (PKG / "ts" / "index.ts").read_text()
    ts_tools = re.search(r"TOOL_NAMES = \[(.*?)\]", ts, re.S).group(1)
    assert re.findall(r'"(\w+)"', ts_tools) == list(TOOLS)
    ts_event = re.search(r"interface DecisionEvent \{(.*?)\}", ts, re.S).group(1)
    assert re.findall(r"(\w+):", ts_event) == [f.alias or n for n, f in DecisionEvent.model_fields.items()]
    ts_claims = re.search(r"interface MandateClaims \{(.*?)\}", ts, re.S).group(1)
    assert re.findall(r"(\w+):", ts_claims) == list(MandateClaims.model_fields)
    from mandate_contracts.roles import ROLE_TOOLS

    assert re.findall(r'"(\w+)"', re.search(r"ROLES = \[(.*?)\]", ts, re.S).group(1)) == list(ROLE_TOOLS)
    ts_role_tools = re.search(r"ROLE_TOOLS: .*? = \{(.*?)\};", ts, re.S).group(1)
    parsed = {m[0]: tuple(re.findall(r'"(\w+)"', m[1])) for m in re.findall(r"(\w+): \[(.*?)\]", ts_role_tools)}
    assert parsed == ROLE_TOOLS
    ts_reason = re.search(r"type Reason =(.*?);", ts, re.S).group(1)
    assert re.findall(r'"([^"]+)"', ts_reason) == list(get_args(Reason))


def test_cedar_context_always_has_every_key():
    ctx = build_context(delegatorActive=True)["contextMap"]
    assert ctx == {
        "delegatorActive": {"boolean": True},
        "recipientKnown": {"boolean": False},
        "account": {"string": ""},
    }
    with pytest.raises(KeyError):
        build_context(office="rep-y")


def test_cedar_policies_cover_every_tool_and_sample_policy_ids():
    text = "\n".join(STARTER_POLICIES.values())
    assert all(f'Mandate::Action::"{name}"' in text for name in TOOLS)
    for raw in json.loads((PKG / "samples" / "events.json").read_text()):
        assert set(raw["policyIds"]) <= set(STARTER_POLICIES)
    committed = {p.stem: p.read_text() for p in (PKG / "policies").glob("*.cedar")}
    assert committed == {k: v + "\n" for k, v in STARTER_POLICIES.items()}


def test_cedar_entities():
    assert agent_entity("agent-1", "rep-x", "official", "caseworker")["attributes"] == {
        "office": {"string": "rep-x"},
        "mode": {"string": "official"},
        "role": {"string": "caseworker"},
    }
    assert case_entity("case-7", "rep-x")["identifier"] == {
        "entityType": "Mandate::Case",
        "entityId": "case-7",
    }


def test_ddb_items():
    email = EmailItem.model_validate({"from": "a@b.gov", "subject": "s", "body": "b"})
    assert email.model_dump(by_alias=True) == {"from": "a@b.gov", "subject": "s", "body": "b", "kind": "other"}
    with pytest.raises(ValueError):
        SessionItem(status="paused", agent="a", acting_for="m", office="rep-x", mode="official", created_at="t")
    session = SessionItem(status="active", agent="a", acting_for="m", office="rep-x", mode="official", created_at="t")
    assert set(session.model_dump()) == {"status", "agent", "actingFor", "office", "mode", "createdAt"}


def test_sample_events_validate():
    for raw in json.loads((PKG / "samples" / "events.json").read_text()):
        assert DecisionEvent.model_validate(raw).model_dump(mode="json") == raw


def test_dev_token_round_trip():
    token = mint_dev_token(SECRET)
    claims = verify_dev_token(token, SECRET)
    assert (claims.sub, claims.act, claims.office, claims.mode) == ("agent-1", "maya", "rep-x", "official")
    assert claims.exp - claims.iat == 15 * 60


def test_dev_token_rejects_tampering_and_expiry():
    with pytest.raises(jwt.PyJWTError):
        verify_dev_token(mint_dev_token(SECRET), "wrong-secret-also-at-least-32-bytes")
    expired = dev_claims(now=int(time.time()) - 3600)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_dev_token(mint_dev_token(SECRET, expired), SECRET)


def test_ddb_keys():
    assert session_key("abc") == {"pk": "SESSION#abc", "sk": "META"}
    assert staffer_key("maya") == {"pk": "STAFFER#maya", "sk": "META"}
    assert case_key("case-7") == {"pk": "CASE#case-7", "sk": "META"}
    assert email_key("email-3") == {"pk": "EMAIL#email-3", "sk": "META"}
    assert event_key("2026-11-07T20:01:05.120Z", "e1") == {
        "pk": "EVENTS#2026-11-07",
        "sk": "2026-11-07T20:01:05.120Z#e1",
    }


def test_role_policies_cover_every_governed_tool_and_every_role_is_a_known_tool_list():
    from mandate_contracts.roles import ROLE_GOVERNED_TOOLS, ROLE_LABELS, ROLE_POLICIES, ROLE_TOOLS, Role

    assert list(ROLE_TOOLS) == list(get_args(Role)) == list(ROLE_LABELS)
    assert set(ROLE_GOVERNED_TOOLS) == {n for n, spec in TOOLS.items() if not spec.always_denied}
    for tools in ROLE_TOOLS.values():
        assert set(tools) <= set(ROLE_GOVERNED_TOOLS)
    assert set(ROLE_TOOLS["chief_of_staff"]) == set(ROLE_GOVERNED_TOOLS)
    assert set(ROLE_POLICIES) <= set(STARTER_POLICIES)
    for name, text in ROLE_POLICIES.items():
        assert text.startswith("forbid(") and "principal has role" in text, name


def test_staffer_item_office_role_is_optional_and_checked():
    from mandate_contracts.ddb import StafferItem

    assert StafferItem(active=True, office="rep-x", role="staffer").office_role is None
    item = StafferItem(active=True, office="rep-x", role="staffer", office_role="intern")
    assert item.model_dump(by_alias=True)["officeRole"] == "intern"
    with pytest.raises(ValueError):
        StafferItem(active=True, office="rep-x", role="staffer", office_role="senator")
