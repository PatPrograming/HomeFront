"""The pieces that let the API run on AWS Lambda (deploy/lambda): state shared through DynamoDB
across processes, runs handed to their own invocation, agents started as ECS tasks, the handler,
and the console served by the API."""

from __future__ import annotations

import importlib
import sys
import threading

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from identity_gateway.runner import RunRecord
from identity_gateway.runtime_repo import (
    RUNTIME_TABLE_SPEC,
    DynamoRunStore,
    DynamoTaintRepository,
    DynamoTokenRepository,
)
from identity_gateway.store import GatewayStore, TokenRecord

from app.services.agent_supervisor import AgentStartError, EcsAgentSupervisor


@pytest.fixture
def table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield resource.create_table(TableName="test-runtime", BillingMode="PAY_PER_REQUEST", **RUNTIME_TABLE_SPEC)


# --- taint and tokens: what one process writes, every other process sees ----------


def two_stores(table) -> tuple[GatewayStore, GatewayStore]:
    stores = (GatewayStore(), GatewayStore())  # two Lambda processes
    for store in stores:
        store.use_taint_repository(DynamoTaintRepository(table))
        store.use_token_repository(DynamoTokenRepository(table))
    return stores


def test_a_taint_mark_reaches_every_process(table):
    a, b = two_stores(table)
    assert b.taint_of("ses_1") is None
    a.mark_tainted("ses_1", "web.scrape")
    assert b.taint_of("ses_1")["tool"] == "web.scrape"
    assert b.taint_of("ses_2") is None


def test_the_first_untrusted_read_stays_the_taint(table):
    a, b = two_stores(table)
    a.mark_tainted("ses_1", "web.scrape")
    b.mark_tainted("ses_1", "sam.entity_lookup")
    assert a.taint_of("ses_1")["tool"] == "web.scrape"


def test_a_workload_token_resolves_in_another_process(table):
    a, b = two_stores(table)
    rec = TokenRecord(token="gr_secret", agent_id="agent-1", token_version=3, key_blob="k")
    a.tokens.put(rec)
    assert b.tokens.get("gr_secret") == rec
    assert b.tokens.get("gr_other") is None
    # Stored under a hash of the token, not the token itself.
    assert all("gr_secret" not in item["id"] for item in table.scan()["Items"])


# --- runs ---------------------------------------------------------------------------


def make_run(run_id: str, agent_id: str = "agent-1", created_at: float = 1.0) -> RunRecord:
    return RunRecord(run_id=run_id, agent_id=agent_id, task="t", model="m", created_by="maya", created_at=created_at)


def test_runs_round_trip_and_list_newest_first(table):
    runs = DynamoRunStore(table)
    runs.add(make_run("run_a", created_at=1.0))
    runs.add(make_run("run_b", created_at=2.0))
    runs.add(make_run("run_c", agent_id="agent-2"))
    later = runs.get("run_a")
    later.status, later.steps = "completed", [{"type": "tool", "ok": True}]
    runs.save(later)
    assert runs.get("run_a").steps == [{"type": "tool", "ok": True}]
    assert [r.run_id for r in runs.for_agent("agent-1")] == ["run_b", "run_a"]
    assert runs.get("run_missing") is None


class FakeRunner:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, run, agent):
        self.executed.append(run.run_id)
        run.status = "completed"
        run.output = "done"
        return run


@pytest.fixture
def api(table, monkeypatch):
    from identity_gateway import agents_api

    monkeypatch.setattr(agents_api, "runs", DynamoRunStore(table))
    runner = FakeRunner()
    monkeypatch.setattr(agents_api, "runner", runner)
    agent = agents_api.store.list_agents()[0]
    return agents_api, runner, agent


def test_a_queued_run_executes_once(api):
    agents_api, runner, agent = api
    agents_api.runs.add(make_run("run_q", agent_id=agent.agent_id))
    agents_api.execute_queued_run("run_q")
    agents_api.execute_queued_run("run_q")  # Lambda delivered it twice
    assert runner.executed == ["run_q"]
    saved = agents_api.runs.get("run_q")
    assert (saved.status, saved.output) == ("completed", "done")


def test_a_run_without_its_agent_is_recorded_as_failed(api):
    agents_api, runner, _ = api
    agents_api.runs.add(make_run("run_x", agent_id="agent-gone"))
    agents_api.execute_queued_run("run_x")
    assert runner.executed == []
    assert agents_api.runs.get("run_x").status == "failed"


# --- agents as ECS tasks ------------------------------------------------------------


class FakeEcs:
    def __init__(self):
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self.status: dict[str, str] = {}

    def run_task(self, **kwargs):
        arn = f"arn:aws:ecs:us-east-1:1:task/homefront/t{len(self.started)}"
        self.started.append(kwargs)
        self.status[arn] = "RUNNING"
        return {"tasks": [{"taskArn": arn}], "failures": []}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [{"taskArn": t, "lastStatus": self.status[t], "desiredStatus": "RUNNING"} for t in tasks]}

    def stop_task(self, cluster, task, reason):
        self.stopped.append(task)
        self.status[task] = "STOPPED"


@pytest.fixture
def ecs_supervisor(table):
    ecs = FakeEcs()
    supervisor = EcsAgentSupervisor(table, ecs, cluster="homefront", task_definition="homefront-agent:1",
                                    subnets=["subnet-1"], security_groups=["sg-1"],
                                    gateway_url=lambda: "https://x.lambda-url.us-east-1.on.aws/mcp", max_starts=2)
    return supervisor, ecs


def test_one_task_per_session_whichever_process_asks(ecs_supervisor, table):
    supervisor, ecs = ecs_supervisor
    assert supervisor.ensure_running("sid-1", "maya", ends_at=9e9) == "started"
    other_process = EcsAgentSupervisor(table, ecs, cluster="homefront", task_definition="homefront-agent:1",
                                       subnets=["subnet-1"], security_groups=["sg-1"], gateway_url=lambda: "")
    assert other_process.ensure_running("sid-1", "maya", ends_at=9e9) == "running"
    assert len(ecs.started) == 1
    override = ecs.started[0]["overrides"]["containerOverrides"][0]
    assert override["command"] == ["--sid", "sid-1", "--chat"]
    assert override["environment"] == [{"name": "MANDATE_GATEWAY_URL", "value": "https://x.lambda-url.us-east-1.on.aws/mcp"}]
    assert ecs.started[0]["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "ENABLED"


def test_concurrent_messages_start_one_task(ecs_supervisor):
    supervisor, ecs = ecs_supervisor
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(supervisor.ensure_running("sid-1", "maya", 9e9)))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ecs.started) == 1
    assert sorted(results) == ["running"] * 4 + ["started"]


def test_a_stopped_agent_restarts_until_the_limit(ecs_supervisor):
    supervisor, ecs = ecs_supervisor
    supervisor.ensure_running("sid-1", "maya", 9e9)
    ecs.status[ecs.started[-1] and next(iter(ecs.status))] = "STOPPED"
    assert supervisor.ensure_running("sid-1", "maya", 9e9) == "started"
    for arn in ecs.status:
        ecs.status[arn] = "STOPPED"
    with pytest.raises(AgentStartError, match="stopped 2 times"):
        supervisor.ensure_running("sid-1", "maya", 9e9)


def test_an_expired_session_starts_nothing(ecs_supervisor):
    supervisor, ecs = ecs_supervisor
    with pytest.raises(AgentStartError, match="expired"):
        supervisor.ensure_running("sid-1", "maya", ends_at=1.0)
    assert ecs.started == []


def test_offboarding_stops_only_that_staffers_agents(ecs_supervisor):
    supervisor, ecs = ecs_supervisor
    supervisor.ensure_running("sid-maya", "maya", 9e9)
    supervisor.ensure_running("sid-sam", "sam", 9e9)
    supervisor.stop_staffer("maya", "staffer offboarded")
    assert ecs.stopped == [ecs.started[0] and "arn:aws:ecs:us-east-1:1:task/homefront/t0"]
    supervisor.stop_all("server shutdown")  # a Lambda process ending stops nobody
    assert len(ecs.stopped) == 1


def test_the_sweep_stops_agents_of_dead_sessions(ecs_supervisor):
    supervisor, ecs = ecs_supervisor
    supervisor.ensure_running("sid-live", "maya", 9e9)
    supervisor.ensure_running("sid-revoked", "maya", 9e9)
    supervisor.sweep(lambda sid: sid == "sid-live")
    assert ecs.stopped == ["arn:aws:ecs:us-east-1:1:task/homefront/t1"]


# --- the Lambda handler ---------------------------------------------------------------


class FakeSecrets:
    def get_secret_value(self, SecretId):
        return {"SecretString": '{"MANDATE_TABLE": "mandate", "AWS_REGION": "eu-west-9", "AGENT_STORE": "dynamodb"}'}


@pytest.fixture
def handler_module(monkeypatch):
    # Importing the handler edits these process-wide; monkeypatch puts them back afterwards.
    monkeypatch.setenv("PUBLIC_URL", "https://abc.lambda-url.us-east-1.on.aws/")
    monkeypatch.setenv("MANDATE_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.delenv("HOMEFRONT_CONFIG_SECRET", raising=False)
    sys.modules.pop("app.lambda_handler", None)
    module = importlib.import_module("app.lambda_handler")
    yield module
    module.agents_api.configure_run_dispatcher(None)  # other tests run runs in-process
    sys.modules.pop("app.lambda_handler", None)


def test_config_fills_gaps_but_never_overrides_lambda(handler_module):
    env = {"HOMEFRONT_CONFIG_SECRET": "arn:secret", "AWS_REGION": "us-east-1"}
    handler_module.load_config(env, client=FakeSecrets())
    assert env["MANDATE_TABLE"] == "mandate"
    assert env["AWS_REGION"] == "us-east-1"


def test_the_function_url_is_allowed_for_mcp_and_cors(handler_module):
    env = {"PUBLIC_URL": "https://abc.lambda-url.us-east-1.on.aws/"}
    assert handler_module.learn_public_url(env) == "https://abc.lambda-url.us-east-1.on.aws"
    assert env["MANDATE_MCP_ALLOWED_HOSTS"].split(",") == ["127.0.0.1:*", "localhost:*", "abc.lambda-url.us-east-1.on.aws"]
    assert env["ALLOWED_ORIGINS"] == "https://abc.lambda-url.us-east-1.on.aws"


def test_job_events_run_and_web_events_reach_the_app(handler_module, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(handler_module.agents_api, "execute_queued_run", ran.append)
    assert handler_module.handler({"homefront_job": "run", "run_id": "run_1"}, None) == {"ok": True}
    assert ran == ["run_1"]

    class Context:
        aws_request_id = "req-1"

    event = {"version": "2.0", "routeKey": "$default", "rawPath": "/api/v1/health", "rawQueryString": "",
             "headers": {"host": "abc.lambda-url.us-east-1.on.aws"},
             "requestContext": {"http": {"method": "GET", "path": "/api/v1/health", "protocol": "HTTP/1.1",
                                         "sourceIp": "1.2.3.4", "userAgent": "t"}, "stage": "$default",
                                "domainName": "abc.lambda-url.us-east-1.on.aws"},
             "isBase64Encoded": False}
    response = handler_module.handler(event, Context())
    assert response["statusCode"] == 200
    assert ran == ["run_1"]


def test_dispatch_invokes_this_function_asynchronously(handler_module, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "homefront-api")
    calls: list[dict] = []

    class FakeLambda:
        def invoke(self, **kwargs):
            calls.append(kwargs)

    handler_module.dispatch_run("run_9", client=FakeLambda())
    assert calls[0]["FunctionName"] == "homefront-api" and calls[0]["InvocationType"] == "Event"
    assert b'"run_id": "run_9"' in calls[0]["Payload"]


# --- the console, served by the API -------------------------------------------------


def test_the_api_serves_the_built_console(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>HomeFront</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert "HomeFront" in client.get("/console").text  # client-side route
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/v1/nope").status_code == 404  # unknown API paths are not the console
    assert "HomeFront" in client.get("/..%2F..%2Fetc%2Fpasswd").text
