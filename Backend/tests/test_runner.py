import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import ADMIN, auth

from identity_gateway import agents_api as gateway  # noqa: E402
from identity_gateway.runner import (  # noqa: E402
    AgentRunner,
    RunnerConfig,
    bedrock_model_id,
)

# Every call signs in as an admin with our Cognito login (tests/helpers.py).
client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login")


def _auth(sub: str) -> dict[str, str]:
    return auth(sub)


# --- scripted fake model ---------------------------------------------------


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(name, inp, id_=None):
    return SimpleNamespace(type="tool_use", id=id_ or f"toolu_{uuid.uuid4().hex[:8]}", name=name, input=inp)


def response(*content, stop="end_turn", **extra):
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        **extra,
    )


class FakeModel:
    """Returns scripted responses; a callable entry is run (to act mid-run)."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        # Snapshot messages: the runner keeps appending to the same list.
        self.calls.append({**params, "messages": list(params["messages"])})
        step = self.script.pop(0)
        return step() if callable(step) else step


@pytest.fixture
def use_model():
    def install(*script, **config):
        fake = FakeModel(*script)
        gateway.configure_runner(
            AgentRunner(
                client_factory=lambda: fake,
                call_tool=gateway.gateway_tool_call,
                issue_token=gateway._issue_run_token,
                config=RunnerConfig(**config),
            )
        )
        return fake

    previous = gateway.runner
    yield install
    gateway.configure_runner(previous)


def _agent(**overrides):
    body = {
        "agent_id": f"agent-run-{uuid.uuid4().hex[:8]}",
        "name": "Housing Researcher",
        "agency": "Dept. of Housing",
        "instructions": "Research housing datasets.",
        "tool_grants": ["datagov.search", "web.scrape"],
        "max_steps": 5,
        **overrides,
    }
    r = client.post("/api/v1/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _run(agent_id, task="Find housing data", headers=None):
    r = client.post(f"/api/v1/agents/{agent_id}/runs", json={"task": task}, headers=headers or {})
    assert r.status_code == 202, r.text
    # TestClient runs background tasks before returning, so the run is done.
    return client.get(f"/api/v1/agents/{agent_id}/runs/{r.json()['run_id']}", headers=headers or {}).json()


def _tool_results(call):
    return call["messages"][-1]["content"]


def test_run_completes_with_gateway_tool_call(use_model):
    fake = use_model(
        response(text("Searching."), tool_use("datagov_search", {"q": "housing"}), stop="tool_use"),
        response(text("Found 1 dataset.")),
    )
    agent = _agent()
    run = _run(agent["agent_id"])

    assert run["status"] == "completed"
    assert run["output"] == "Found 1 dataset."
    assert run["model"] == "anthropic.claude-opus-5-5"
    assert run["model_turns"] == 2
    assert run["usage"] == {"input_tokens": 200, "output_tokens": 40}
    [step] = run["steps"]
    assert step["tool"] == "datagov.search" and step["decision"] == "allow"

    # Request shape sent to the model.
    first = fake.calls[0]
    assert first["model"] == "anthropic.claude-opus-5-5"
    assert first["output_config"] == {"effort": "medium"}
    assert "Housing Researcher" in first["system"]
    assert "Research housing datasets." in first["system"]
    names = sorted(t["name"] for t in first["tools"])
    assert names == ["datagov_search", "web_scrape"]
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in first["tools"])
    assert first["messages"] == [{"role": "user", "content": "Find housing data"}]

    # Tool result went back paired with its tool_use id, as JSON.
    [result] = _tool_results(fake.calls[1])
    assert result["type"] == "tool_result" and "is_error" not in result
    assert json.loads(result["content"])["query"] == "housing"

    # Audited under the run.
    audit = client.get("/api/v1/audit").json()
    assert any(e.get("run_id") == run["run_id"] and e["decision"] == "allow" for e in audit)


def test_research_run_filters_tools_by_topic(use_model):
    fake = use_model(
        response(tool_use("census_query", {
            "year": "2023",
            "dataset": "acs/acs1",
            "get": "NAME,B01003_001E",
            "for": "place:*",
            "in": "state:06",
        }), stop="tool_use"),
        response(text("Population estimate ready.")),
    )
    agent = _agent(
        kind="research",
        name="Agency Research",
        tool_grants=[
            "web.scrape",
            "datagov.search",
            "census.query",
            "nasa.search",
            "nps.parks",
        ],
    )
    run = _run(agent["agent_id"], task="What is the population of Santa Clara?")

    assert run["status"] == "completed"
    route = run["steps"][0]
    assert route["type"] == "topic_route"
    assert "demographics" in route["topics"]
    assert route["tools"] == ["census.query", "datagov.search", "web.scrape"]

    first = fake.calls[0]
    names = sorted(t["name"] for t in first["tools"])
    assert names == ["census_query", "datagov_search", "web_scrape"]
    assert "Topic router matched" in first["system"]
    assert "nasa" not in names and "nps" not in names


def test_revoke_mid_run_blocks_next_call_and_model_is_told(use_model):
    holder = {}

    def revoke_then_call():
        client.post(f"/api/v1/agents/{holder['id']}/grants/web.scrape/revoke")
        return response(tool_use("web_scrape", {"url": "https://example.gov"}), stop="tool_use")

    fake = use_model(
        response(tool_use("web_scrape", {"url": "https://example.gov"}), stop="tool_use"),
        revoke_then_call,
        response(text("I could not scrape after access was revoked.")),
    )
    holder["id"] = _agent()["agent_id"]
    run = _run(holder["id"])

    assert run["status"] == "completed"
    assert [s["decision"] for s in run["steps"]] == ["allow", "deny"]
    assert run["steps"][1]["error"] == "tool_not_granted"
    [denied] = _tool_results(fake.calls[2])
    assert denied["is_error"] is True
    assert json.loads(denied["content"])["error"] == "tool_not_granted"


def test_grant_reset_mid_run_invalidates_token_and_stops(use_model):
    holder = {}

    def reset_then_call():
        client.put(f"/api/v1/agents/{holder['id']}/grants", json={"tools": ["web.scrape"]})
        return response(
            tool_use("datagov_search", {"q": "a"}), tool_use("web_scrape", {"url": "x"}), stop="tool_use"
        )

    use_model(reset_then_call)
    holder["id"] = _agent()["agent_id"]
    run = _run(holder["id"])

    assert run["status"] == "revoked"
    assert [s["error"] for s in run["steps"]] == ["token_revoked", "token_revoked"]


def test_max_steps_caps_the_loop(use_model):
    loop = [response(tool_use("datagov_search", {"q": "again"}), stop="tool_use") for _ in range(3)]
    fake = use_model(*loop)
    run = _run(_agent(max_steps=3)["agent_id"])
    assert run["status"] == "max_steps"
    assert len(fake.calls) == 3


def test_refusal_ends_run(use_model):
    use_model(response(stop="refusal", stop_details=SimpleNamespace(category="cyber")))
    run = _run(_agent()["agent_id"])
    assert run["status"] == "refused"
    assert "cyber" in run["error"]


def test_tool_outside_offered_set_is_rejected(use_model):
    use_model(
        response(tool_use("email_send", {"to": "a", "subject": "b", "body": "c"}), stop="tool_use"),
        response(text("ok")),
    )
    run = _run(_agent()["agent_id"])
    assert run["steps"][0] == {
        "type": "tool_call",
        "tool": "email_send",
        "input": {"to": "a", "subject": "b", "body": "c"},
        "decision": "deny",
        "error": "unknown_tool",
    }


def test_pause_turn_continues(use_model):
    fake = use_model(response(text("thinking..."), stop="pause_turn"), response(text("done")))
    run = _run(_agent()["agent_id"])
    assert run["status"] == "completed" and len(fake.calls) == 2


def test_model_error_marks_run_failed(use_model):
    def boom():
        raise RuntimeError("bedrock unavailable")

    use_model(boom)
    run = _run(_agent()["agent_id"])
    assert run["status"] == "failed" and "bedrock unavailable" in run["error"]


def test_agent_model_override_gets_bedrock_prefix(use_model):
    fake = use_model(response(text("hi")))
    run = _run(_agent(model="claude-sonnet-5-5")["agent_id"])
    assert run["model"] == fake.calls[0]["model"] == "anthropic.claude-sonnet-5-5"


def test_bedrock_model_id():
    assert bedrock_model_id("claude-opus-5-5") == "anthropic.claude-opus-5-5"
    assert bedrock_model_id("anthropic.claude-opus-5-5") == "anthropic.claude-opus-5-5"
    assert bedrock_model_id("us.anthropic.claude-opus-5-5") == "us.anthropic.claude-opus-5-5"


def test_runs_are_owner_scoped(use_model):
    use_model(response(text("done")))
    alice, bob = _auth("00000000-0000-0000-0000-00000000a11c"), _auth("00000000-0000-0000-0000-000000000b0b")
    r = client.post("/api/v1/agents", json={"name": "A", "agency": "B"}, headers=alice)
    aid = r.json()["agent_id"]
    run = _run(aid, headers=alice)
    assert run["created_by"] == "00000000-0000-0000-0000-00000000a11c"

    assert client.post(f"/api/v1/agents/{aid}/runs", json={"task": "x"}, headers=bob).status_code == 404
    assert client.get(f"/api/v1/agents/{aid}/runs", headers=bob).status_code == 404
    assert client.get(f"/api/v1/agents/{aid}/runs/{run['run_id']}", headers=bob).status_code == 404
    assert [r["run_id"] for r in client.get(f"/api/v1/agents/{aid}/runs", headers=alice).json()] == [
        run["run_id"]
    ]


def test_run_id_must_match_agent(use_model):
    use_model(response(text("done")))
    a, b = _agent()["agent_id"], _agent()["agent_id"]
    run = _run(a)
    assert client.get(f"/api/v1/agents/{b}/runs/{run['run_id']}").status_code == 404


def test_runner_not_configured_is_503():
    previous = gateway.runner
    gateway.configure_runner(None)
    try:
        r = client.post(f"/api/v1/agents/{_agent()['agent_id']}/runs", json={"task": "x"})
        assert r.status_code == 503
    finally:
        gateway.configure_runner(previous)


def test_inactive_agent_cannot_run(use_model):
    use_model()
    aid = _agent()["agent_id"]
    client.patch(f"/api/v1/agents/{aid}", json={"active": False})
    assert client.post(f"/api/v1/agents/{aid}/runs", json={"task": "x"}).status_code == 403


# --- wire level: real Bedrock Mantle client, mocked HTTP --------------------


def test_bedrock_wire_format(monkeypatch):
    # Wire-level tests need the anthropic SDK and httpx2 (requirements.txt); skipped
    # in a venv without them rather than reaching the network.
    pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    from anthropic import AnthropicBedrockMantle, BetaFallbackState, BetaRefusalFallbackMiddleware

    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        body = json.loads(request.content)
        base = {
            "id": f"msg_{len(requests)}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "stop_sequence": None,
            "usage": {"input_tokens": 50, "output_tokens": 10},
        }
        if len(requests) == 1:
            return httpx2.Response(
                200,
                json={
                    **base,
                    "stop_reason": "tool_use",
                    "content": [
                        {"type": "thinking", "thinking": "", "signature": "sig"},
                        {"type": "tool_use", "id": "toolu_1", "name": "datagov_search", "input": {"q": "parks"}},
                    ],
                },
            )
        return httpx2.Response(
            200, json={**base, "stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}
        )

    def make_client():
        return AnthropicBedrockMantle(
            aws_region="us-east-1",
            api_key="test-bedrock-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
            middleware=[BetaRefusalFallbackMiddleware([{"model": "anthropic.claude-opus-4-8"}])],
            max_retries=0,
        )

    previous = gateway.runner
    gateway.configure_runner(
        AgentRunner(
            client_factory=make_client,
            call_tool=gateway.gateway_tool_call,
            issue_token=gateway._issue_run_token,
            fallback_state_factory=BetaFallbackState,
        )
    )
    try:
        run = _run(_agent()["agent_id"], task="Find park datasets")
    finally:
        gateway.configure_runner(previous)

    assert run["status"] == "completed", run
    assert run["output"] == "Done."
    assert len(requests) == 2

    req = requests[0]
    assert str(req.url) == "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages"
    assert "fallback-credit-2026-07-01" in req.headers.get("anthropic-beta", "")
    body = json.loads(req.content)
    assert body["model"] == "anthropic.claude-opus-5-5"
    assert body["output_config"] == {"effort": "medium"}
    assert "thinking" not in body  # Opus 5.5: always adaptive, omit the param
    tool = next(t for t in body["tools"] if t["name"] == "datagov_search")
    assert tool["strict"] is True
    assert tool["input_schema"]["required"] == ["q"]

    # Second request replays the assistant turn unchanged (thinking block
    # included) followed by the paired tool result.
    second = json.loads(requests[1].content)["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert [b["type"] for b in second[1]["content"]] == ["thinking", "tool_use"]
    assert second[1]["content"][0]["signature"] == "sig"
    [result] = second[2]["content"]
    assert result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"])["query"] == "parks"


def test_bedrock_refusal_falls_back_and_pins():
    # Wire-level tests need the anthropic SDK and httpx2 (requirements.txt); skipped
    # in a venv without them rather than reaching the network.
    pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    from anthropic import AnthropicBedrockMantle, BetaFallbackState, BetaRefusalFallbackMiddleware

    models: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        models.append(body["model"])
        base = {
            "id": f"msg_{len(models)}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 1},
        }
        if body["model"] == "anthropic.claude-opus-5-5":
            return httpx2.Response(
                200,
                json={
                    **base,
                    "stop_reason": "refusal",
                    "stop_details": {
                        "type": "refusal",
                        "category": "cyber",
                        "explanation": None,
                        "fallback_credit_token": "tok_123",
                        "fallback_has_prefill_claim": False,
                    },
                    "content": [],
                },
            )
        if len(models) == 2:  # fallback's first turn: call a tool
            return httpx2.Response(
                200,
                json={
                    **base,
                    "stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "toolu_9", "name": "datagov_search", "input": {"q": "x"}}],
                },
            )
        return httpx2.Response(
            200, json={**base, "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}
        )

    previous = gateway.runner
    gateway.configure_runner(
        AgentRunner(
            client_factory=lambda: AnthropicBedrockMantle(
                aws_region="us-east-1",
                api_key="k",
                http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
                middleware=[BetaRefusalFallbackMiddleware([{"model": "anthropic.claude-opus-4-8"}])],
                max_retries=0,
            ),
            call_tool=gateway.gateway_tool_call,
            issue_token=gateway._issue_run_token,
            fallback_state_factory=BetaFallbackState,
        )
    )
    try:
        run = _run(_agent()["agent_id"])
    finally:
        gateway.configure_runner(previous)

    assert run["status"] == "completed", run
    # Refused once, then the fallback served and stayed pinned for turn 2.
    assert models == [
        "anthropic.claude-opus-5-5",
        "anthropic.claude-opus-4-8",
        "anthropic.claude-opus-4-8",
    ]
    assert {"type": "model_fallback", "from": "anthropic.claude-opus-5-5", "to": "anthropic.claude-opus-4-8"} in run["steps"]
