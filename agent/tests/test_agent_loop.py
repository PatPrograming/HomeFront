"""The agent loop against the stub gateway with a scripted model (check C6). No Bedrock call."""

from collections import Counter

import httpx
from conftest import TOKEN
from fake_model import ScriptedModel
from mandate_agent.config import Settings
from mandate_agent.runner import EXIT_OK, EXIT_STOPPED, run_inbox

OUTSIDE = "archive@civic-records-backup.com"


def run(stub, script):
    url, base, state = stub
    lines = []
    model = ScriptedModel(script)
    code, guard = run_inbox("Work the inbox.", TOKEN, Settings(url, base, "scripted", "us-east-1"),
                            model=model, out=lines.append)
    return code, guard, model, "\n".join(lines), state


def tools_reaching_gateway(state):
    return Counter(call["tool"] for call in state.calls)


def tool_results_seen_by_model(model):
    """Tool results in the last prompt; each prompt carries the whole conversation so far."""
    return [block["toolResult"] for message in model.seen[-1] for block in message["content"] if "toolResult" in block]


def test_denied_is_reported_to_the_model_and_not_retried(stub):
    code, guard, model, output, state = run(stub, [
        [("inbox_read", {"email_id": "email-3"})],
        [("records_bulk_export", {"query": "all constituent cases rep-x"})],
        [("records_bulk_export", {"query": "all cases"})],  # retry with other wording
        [("email_send", {"to": OUTSIDE, "subject": "Export", "body": "attached"})],
        [("email_send", {"to": OUTSIDE, "subject": "Export (again)", "body": "please"})],  # retry
        [("social_post", {"account": "campaign", "text": "Volunteer!"})],
        "Summary: the bulk export, the outside email and the campaign post were denied.",
    ])
    assert code == EXIT_OK
    reached = tools_reaching_gateway(state)
    assert reached["records_bulk_export"] == 1 and reached["email_send"] == 1 and reached["social_post"] == 1
    assert len(guard.blocked_retries) == 2
    assert [o.tool for o in guard.denied] == ["records_bulk_export", "email_send", "social_post"]
    denied_texts = [r["content"][0]["text"] for r in tool_results_seen_by_model(model) if r["status"] == "error"]
    assert any(t.startswith("DENIED (denied by policy") and "NOT performed" in t for t in denied_texts)
    assert sum("already DENIED" in t for t in denied_texts) == 2


def test_success_is_never_claimed_for_a_denied_call(stub):
    # Even if the model's own summary claims success, the printed ledger says what happened.
    code, guard, _, output, _ = run(stub, [
        [("case_read", {"case_id": "case-1"})],
        [("records_bulk_export", {"query": "all"})],
        "Done! I exported all the records.",
    ])
    assert [o.tool for o in guard.done] == ["case_read"]
    report = output.split("--- What actually happened")[1]
    assert "ALLOW  case_read" in report
    assert "DENY   records_bulk_export" in report and "ALLOW  records_bulk_export" not in report


def test_revoked_session_stops_the_run(stub):
    url, base, state = stub
    state.revoke_after = 1  # Maya is offboarded after the first call
    code, guard, model, output, state = run(stub, [
        [("case_read", {"case_id": "case-1"})],
        [("case_read", {"case_id": "case-2"})],
        [("email_send", {"to": "rosa.delgado@example.com", "subject": "s", "body": "b"})],
        "This turn must never happen.",
    ])
    assert code == EXIT_STOPPED
    assert len(state.calls) == 2  # nothing after the revoked call reached the gateway
    assert len(model.seen) == 2  # the model was not asked again after the revocation
    assert "session revoked / delegator inactive" in guard.stop_reason
    assert "Stopping:" in output and "Run stopped:" in output


def test_http_401_mid_run_stops_the_run(stub):
    url, base, state = stub

    def offboard_with_401():
        httpx.post(f"{base}/admin/revoke?mode=401")
        return [("case_read", {"case_id": "case-2"})]

    code, guard, model, output, state = run(stub, [
        [("case_read", {"case_id": "case-1"})],
        offboard_with_401,
        [("case_read", {"case_id": "case-3"})],
        "This turn must never happen.",
    ])
    assert code == EXIT_STOPPED
    assert guard.unauthorized and "HTTP 401" in guard.stop_reason
    assert [c["args"]["case_id"] for c in state.calls] == ["case-1"]
    assert len(model.seen) == 2


def test_http_401_at_start_stops_cleanly(stub):
    url, base, state = stub
    httpx.post(f"{base}/admin/revoke?mode=401")
    code, guard, model, output, _ = run(stub, ["never used"])
    assert code == EXIT_STOPPED
    assert "HTTP 401" in output and model.seen == []


def test_bearer_header_is_sent_on_every_mcp_request(stub):
    code, _, _, _, state = run(stub, [
        [("inbox_read", {"email_id": "email-1"}), ("case_read", {"case_id": "case-13"})],
        [("email_send", {"to": "marcus.bell@example.com", "subject": "Post office", "body": "We are on it."})],
        "Replied to Marcus.",
    ])
    assert code == EXIT_OK and len(state.calls) == 3
    assert len(state.auth_headers) >= 4  # initialize, list tools, 3 calls ...
    assert set(state.auth_headers) == {f"Bearer {TOKEN}"}
