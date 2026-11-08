"""Offline checks of deploy/aws: least-privilege role, secrets only from Secrets Manager, CloudFront
routing, and that the shell scripts change nothing without --apply. No AWS call is made: a fake
`aws` CLI on PATH records every call."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import add_tester  # noqa: E402
import cognito_urls  # noqa: E402
import put_secrets  # noqa: E402


class _CfnLoader(yaml.SafeLoader):
    """Reads !Ref, !Sub, !GetAtt ... as {"Ref": ...} style dicts."""


def _tag(loader, suffix, node):
    name = "Ref" if suffix == "Ref" else f"Fn::{suffix}"
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {name: value}


_CfnLoader.add_multi_constructor("!", _tag)


def template(name: str) -> dict:
    return yaml.load((HERE / name).read_text(), Loader=_CfnLoader)


APP = template("app.yaml")
FOUNDATION = template("foundation.yaml")
RES = APP["Resources"]


def statements(policy_name: str) -> list[dict]:
    for policy in RES["TaskRole"]["Properties"]["Policies"]:
        if isinstance(policy, dict) and policy.get("PolicyName") == policy_name:
            return policy["PolicyDocument"]["Statement"]
        if isinstance(policy, dict) and "Fn::If" in policy:
            _, when_true, _ = policy["Fn::If"]
            if when_true.get("PolicyName") == policy_name:
                return when_true["PolicyDocument"]["Statement"]
    raise AssertionError(f"no policy {policy_name}")


def as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def text(value) -> str:
    return json.dumps(value)


# ---------------------------------------------------------------- IAM


def test_no_access_keys_or_users_anywhere():
    for tpl in (APP, FOUNDATION):
        types = {r["Type"] for r in tpl["Resources"].values()}
        assert not types & {"AWS::IAM::AccessKey", "AWS::IAM::User"}


def test_task_role_grants_only_the_listed_actions():
    actions = {a for s in statements("homefront-gateway") for a in as_list(s["Action"])}
    assert actions == {
        "dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
        "dynamodb:DeleteItem", "dynamodb:BatchWriteItem", "dynamodb:ConditionCheckItem",
        "dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeTable",
        "verifiedpermissions:IsAuthorized",
        "sts:AssumeRole",
        "bedrock:InvokeModel",
        "bedrock:ApplyGuardrail",
    }
    for s in statements("homefront-gateway"):
        assert s["Effect"] == "Allow"
        assert "*" not in as_list(s["Resource"]), s["Sid"]


def test_each_statement_is_pinned_to_its_resource():
    by_sid = {s["Sid"]: text(s["Resource"]) for s in statements("homefront-gateway")}
    assert "table/${MandateTable}" in by_sid["TablesItemsOnly"]
    assert "table/${TablePrefix}*" in by_sid["TablesItemsOnly"]
    assert APP["Parameters"]["MandateTable"]["Default"] == "mandate"
    assert APP["Parameters"]["TablePrefix"]["Default"] == "homefront-"
    assert "policy-store/${PolicyStoreId}" in by_sid["CedarDecisions"]
    assert APP["Parameters"]["PolicyStoreId"]["Default"] == "Fmx8fLovCMpXgABUM1bHhb"
    assert "guardrail/${GuardrailId}" in by_sid["GuardrailScan"]
    assert APP["Parameters"]["GuardrailId"]["Default"] == "l74ju5pcrcdg"
    assert by_sid["BecomeTheAgentRoleForEachAgent"] == text({"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${AgentRoleName}"})
    assert APP["Parameters"]["AgentRoleName"]["Default"] == "homefront-agent"


def test_the_gateway_and_the_agents_do_not_share_an_identity():
    # The gateway calls only Haiku 5.5 (conversation titles); the agents' Sonnet 5 is homefront-agent's.
    every_action = text(RES["TaskRole"]["Properties"]["Policies"])
    assert "InvokeModelWithResponseStream" not in every_action and "sonnet" not in every_action
    (titles,) = [st for st in statements("homefront-gateway") if "bedrock:InvokeModel" in as_list(st["Action"])]
    assert titles["Sid"] == "ConversationTitlesHaikuOnly" and titles["Action"] == "bedrock:InvokeModel"
    resources = [r["Fn::Sub"] for r in titles["Resource"]]
    assert resources == [
        "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.anthropic.claude-haiku-5-5*",
        "arn:${AWS::Partition}:bedrock:*::foundation-model/anthropic.claude-haiku-5-5*",
    ]
    assert RES["TaskRole"]["Properties"]["RoleName"] == "homefront-gateway-task"
    env = {v["Name"]: v["Value"] for v in CONTAINER["Environment"]}
    assert env["HOMEFRONT_AGENT_ROLE_ARN"] == {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${AgentRoleName}"}
    supervisor = (HERE.parents[1] / "Backend/app/services/agent_supervisor.py").read_text()
    assert "AWS_CONTAINER_CREDENTIALS" not in supervisor  # the child never inherits the task role


def test_the_stack_and_lane_es_agent_role_script_name_the_same_roles():
    script = HERE.parents[1] / "Backend/infra/agent-role.sh"
    if not script.exists():
        pytest.skip("Lane E's Backend/infra/agent-role.sh is not merged into this checkout")
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=True,
                         env={"PATH": os.environ["PATH"], "AWS_ACCOUNT_ID": "111122223333"}).stdout
    trust = json.loads(out.split("--- trust policy ---\n")[1].split("\n--- permissions")[0])
    (statement,) = trust["Statement"]
    assert statement["Condition"]["ArnEquals"]["aws:PrincipalArn"] == [
        "arn:aws:iam::111122223333:role/" + RES["TaskRole"]["Properties"]["RoleName"]]
    agent_role = APP["Parameters"]["AgentRoleName"]["Default"]
    assert f"HOMEFRONT_AGENT_ROLE_ARN=arn:aws:iam::111122223333:role/{agent_role}" in out
    (assume,) = [st for st in statements("homefront-gateway") if "sts:AssumeRole" in as_list(st["Action"])]
    assert assume["Action"] == "sts:AssumeRole"  # nothing more for the agent role: no TagSession, no PassRole
    assert assume["Resource"] == {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${AgentRoleName}"}
    assert "sts:" not in text([st for st in statements("homefront-gateway") if st is not assume])


def test_connections_key_is_opt_in_and_needs_the_encryption_context():
    policy = next(p for p in RES["TaskRole"]["Properties"]["Policies"]
                  if "Fn::If" in p and p["Fn::If"][0] == "HasConnectionsKey")
    _, granted, otherwise = policy["Fn::If"]
    assert otherwise == {"Ref": "AWS::NoValue"}
    (stmt,) = granted["PolicyDocument"]["Statement"]
    assert set(stmt["Action"]) == {"kms:Encrypt", "kms:Decrypt"}
    assert stmt["Resource"] == {"Ref": "ConnectionsKmsKeyArn"}
    assert stmt["Condition"]["Null"] == {"kms:EncryptionContextKeys": "false"}
    assert stmt["Condition"]["ForAllValues:StringEquals"] == {"kms:EncryptionContextKeys": ["staffer", "provider"]}


def test_ses_is_granted_only_when_a_sender_is_configured_and_only_to_verified_identities():
    policy = next(p for p in RES["TaskRole"]["Properties"]["Policies"] if "Fn::If" in p)
    condition, _, otherwise = policy["Fn::If"]
    assert condition == "HasSes" and otherwise == {"Ref": "AWS::NoValue"}
    send = next(s for s in statements("homefront-ses") if s["Action"] == "ses:SendEmail")
    assert ":identity/*" in text(send["Resource"]) and "Condition" not in send


def test_cognito_admin_calls_are_opt_in_and_pinned_to_the_pool():
    policy = next(p for p in RES["TaskRole"]["Properties"]["Policies"]
                  if "Fn::If" in p and p["Fn::If"][0] == "HasCognitoAdmin")
    _, granted, otherwise = policy["Fn::If"]
    assert otherwise == {"Ref": "AWS::NoValue"}
    assert APP["Parameters"]["GrantCognitoAdmin"]["Default"] == "false"
    (stmt,) = granted["PolicyDocument"]["Statement"]
    assert set(stmt["Action"]) == {"cognito-idp:ListUsers", "cognito-idp:ListUsersInGroup",
                                   "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminRemoveUserFromGroup"}
    assert "userpool/${CognitoUserPoolId}" in text(stmt["Resource"])


def test_the_task_role_trusts_only_ecs_tasks_in_this_account():
    trust = RES["TaskRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
    assert "aws:SourceAccount" in trust["Condition"]["StringEquals"]


def test_the_execution_role_reads_only_the_runtime_secret():
    policy = RES["ExecutionRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"][0]
    assert policy["Action"] == "secretsmanager:GetSecretValue"
    assert policy["Resource"] == {"Ref": "SecretArn"}


# ---------------------------------------------------------------- secrets


CONTAINER = RES["GatewayTaskDefinition"]["Properties"]["ContainerDefinitions"][0]


def test_secrets_come_from_secrets_manager_and_match_put_secrets():
    names = [s["Name"] for s in CONTAINER["Secrets"]]
    assert names == list(put_secrets.SECRET_KEYS)
    for s in CONTAINER["Secrets"]:
        assert s["ValueFrom"] == {"Fn::Sub": "${SecretArn}:" + s["Name"] + "::"}


def test_no_secret_in_plain_environment():
    for var in CONTAINER["Environment"]:
        assert not any(word in var["Name"] for word in ("SECRET", "API_KEY", "TOKEN", "PASSWORD")), var["Name"]
        assert var["Name"] not in put_secrets.SECRET_KEYS


def test_signing_secret_is_generated_inside_aws():
    gen = FOUNDATION["Resources"]["RuntimeSecret"]["Properties"]["GenerateSecretString"]
    assert gen["GenerateStringKey"] == "MANDATE_SIGNING_SECRET"
    assert "SecretString" not in FOUNDATION["Resources"]["RuntimeSecret"]["Properties"]


def test_the_image_never_receives_an_env_file():
    ignore = (HERE / "Dockerfile.dockerignore").read_text().splitlines()
    assert ignore[ignore.index("**") if "**" in ignore else 0] == "**"  # allow-list
    assert "**/.env" in ignore and "**/.env.*" in ignore
    dockerfile = (HERE / "Dockerfile").read_text()
    assert ".env" not in "\n".join(l for l in dockerfile.splitlines() if l.startswith(("COPY", "ADD", "ENV")))


def test_entrypoint_writes_the_agent_file_privately_and_drops_the_secret():
    entry = (HERE / "entrypoint.sh").read_text()
    assert "umask 077" in entry
    assert entry.index("cat > /app/agent/.env") < entry.index("unset COGNITO_AGENT_CLIENT_SECRET") < entry.index("exec ")
    assert "echo $" not in entry and "set -x" not in entry


# ---------------------------------------------------------------- network and CloudFront


def test_gateway_reachable_only_through_cloudfront():
    assert RES["Alb"]["Properties"]["Scheme"] == "internal"
    alb_in = RES["AlbSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert [r.get("SourcePrefixListId") for r in alb_in] == [{"Ref": "CloudFrontPrefixListId"}]
    task_in = RES["TaskSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert [r.get("SourceSecurityGroupId") for r in task_in] == [{"Ref": "AlbSecurityGroup"}]
    assert all("CidrIp" not in r for r in alb_in + task_in)


def test_api_and_mcp_go_to_the_gateway_uncached_over_https():
    config = RES["Distribution"]["Properties"]["DistributionConfig"]
    behaviors = {b["PathPattern"]: b for b in config["CacheBehaviors"]}
    for path in ("/api/*", "/mcp*"):
        b = behaviors[path]
        assert b["TargetOriginId"] == "gateway"
        assert b["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"  # CachingDisabled
        assert b["ViewerProtocolPolicy"] == "https-only"
        assert "POST" in b["AllowedMethods"]
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert config["DefaultCacheBehavior"]["TargetOriginId"] == "console"


def test_console_bucket_is_private_and_read_only_by_this_distribution():
    block = RES["ConsoleBucket"]["Properties"]["PublicAccessBlockConfiguration"]
    assert all(block.values())
    stmt = RES["ConsoleBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]
    assert stmt["Action"] == "s3:GetObject"
    assert "distribution/${Distribution}" in text(stmt["Condition"])


def test_external_mcp_passes_the_dns_rebinding_check():
    config = RES["Distribution"]["Properties"]["DistributionConfig"]
    mcp = next(b for b in config["CacheBehaviors"] if b["PathPattern"] == "/mcp*")
    assert mcp["OriginRequestPolicyId"] == "216adef6-5c7f-47e4-b989-5492eafa07d3"  # AllViewer: Host = CloudFront name
    env = {v["Name"]: v["Value"] for v in CONTAINER["Environment"]}
    assert env["MANDATE_MCP_ALLOWED_HOSTS"] == {"Fn::Sub": "127.0.0.1:*,localhost:*,${Distribution.DomainName}"}


def test_allowed_origins_add_https_and_keep_localhost():
    env = {v["Name"]: v["Value"] for v in CONTAINER["Environment"]}
    assert env["ALLOWED_ORIGINS"] == {"Fn::Sub": "https://${Distribution.DomainName},http://localhost:3000"}
    assert env["HOMEFRONT_AUTOSTART_AGENT"] == "1"


def test_one_task_with_init_and_health_check():
    svc = RES["GatewayService"]["Properties"]
    assert svc["DesiredCount"] == 1 and svc["DeploymentConfiguration"]["MaximumPercent"] == 100
    assert CONTAINER["LinuxParameters"]["InitProcessEnabled"] is True
    assert "/api/v1/health" in " ".join(CONTAINER["HealthCheck"]["Command"])
    assert RES["GatewayTargetGroup"]["Properties"]["HealthCheckPath"] == "/api/v1/health"
    assert CONTAINER["LogConfiguration"]["LogDriver"] == "awslogs"


# ---------------------------------------------------------------- put_secrets.py


class FakeCognito:
    def __init__(self, client: dict):
        self.client = client
        self.updates: list[dict] = []

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return {"UserPoolClient": {**self.client, "UserPoolId": UserPoolId, "ClientId": ClientId}}

    def update_user_pool_client(self, **kwargs):
        self.updates.append(kwargs)


class FakeSecrets:
    def __init__(self, value: dict):
        self.value = value
        self.puts: list[dict] = []

    def get_secret_value(self, SecretId):
        return {"SecretString": json.dumps(self.value)}

    def put_secret_value(self, SecretId, SecretString):
        self.puts.append(json.loads(SecretString))


def test_put_secrets_dry_run_writes_nothing_and_prints_no_value(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("DATA_GOV_API_KEY='gov-value-123'\nMANDATE_SIGNING_SECRET=from-env-ignored\n")
    secrets = FakeSecrets({"MANDATE_SIGNING_SECRET": "generated-in-aws"})
    put_secrets.main(["--env-file", str(env)], FakeCognito({"ClientSecret": "agent-secret-xyz"}), secrets)
    out = capsys.readouterr().out
    assert secrets.puts == []
    assert "DRY RUN" in out and "DATA_GOV_API_KEY: set" in out and "CENSUS_API_KEY: empty" in out
    for value in ("gov-value-123", "generated-in-aws", "agent-secret-xyz", "from-env-ignored"):
        assert value not in out


def test_put_secrets_apply_keeps_the_generated_signing_secret_and_fills_every_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DATA_GOV_API_KEY=gov\nMANDATE_SIGNING_SECRET=from-env-ignored\n")
    secrets = FakeSecrets({"MANDATE_SIGNING_SECRET": "generated-in-aws"})
    put_secrets.main(["--env-file", str(env), "--apply"], FakeCognito({"ClientSecret": "agent"}), secrets)
    (written,) = secrets.puts
    assert set(written) == set(put_secrets.SECRET_KEYS)
    assert written["MANDATE_SIGNING_SECRET"] == "generated-in-aws"
    assert written["COGNITO_AGENT_CLIENT_SECRET"] == "agent"
    assert written["DATA_GOV_API_KEY"] == "gov" and written["NASA_API_KEY"] == ""


def test_put_secrets_refuses_without_a_generated_signing_secret(tmp_path):
    with pytest.raises(SystemExit):
        put_secrets.main(["--env-file", str(tmp_path / "none"), "--apply"],
                         FakeCognito({"ClientSecret": "a"}), FakeSecrets({}))


# ---------------------------------------------------------------- cognito_urls.py


STAFF_CLIENT = {
    "ClientName": "staff-web", "ClientSecret": "never-sent-back", "CreationDate": "x",
    "AllowedOAuthFlows": ["code"], "AllowedOAuthScopes": ["openid", "email"],
    "AllowedOAuthFlowsUserPoolClient": True, "SupportedIdentityProviders": ["COGNITO"],
    "CallbackURLs": ["http://localhost:3000/"], "LogoutURLs": ["http://localhost:3000/"],
}
CF = "https://d111.cloudfront.net/"


def test_cognito_urls_adds_https_and_keeps_every_other_setting():
    request = cognito_urls.update_request({**STAFF_CLIENT, "UserPoolId": "p", "ClientId": "c"}, CF)
    assert request["CallbackURLs"] == ["http://localhost:3000/", CF]
    assert request["LogoutURLs"] == ["http://localhost:3000/", CF]
    assert request["AllowedOAuthFlows"] == ["code"] and request["AllowedOAuthScopes"] == ["openid", "email"]
    assert "ClientSecret" not in request and "CreationDate" not in request


def test_cognito_urls_remove_keeps_localhost():
    client = {**STAFF_CLIENT, "UserPoolId": "p", "ClientId": "c", "CallbackURLs": ["http://localhost:3000/", CF]}
    assert cognito_urls.update_request(client, CF, remove=True)["CallbackURLs"] == ["http://localhost:3000/"]


def test_cognito_urls_dry_run_changes_nothing_and_apply_updates_once(capsys):
    fake = FakeCognito(STAFF_CLIENT)
    cognito_urls.main(["https://d111.cloudfront.net"], fake)
    assert fake.updates == [] and "DRY RUN" in capsys.readouterr().out
    cognito_urls.main(["https://d111.cloudfront.net", "--apply"], fake)
    assert len(fake.updates) == 1 and fake.updates[0]["CallbackURLs"][-1] == CF


def test_cognito_urls_rejects_plain_http():
    with pytest.raises(SystemExit):
        cognito_urls.main(["http://d111.cloudfront.net/"], FakeCognito(STAFF_CLIENT))


# ---------------------------------------------------------------- add_tester.py


class FakeAdminCognito:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def admin_create_user(self, **kwargs):
        self.calls.append(("create", kwargs))

    def admin_add_user_to_group(self, **kwargs):
        self.calls.append(("group", kwargs))


class FakeStore:
    def __init__(self):
        self.staffers: dict = {}

    def put_staffer(self, staffer_id, item):
        self.staffers[staffer_id] = item


def test_add_tester_dry_run_creates_nothing():
    cognito, store = FakeAdminCognito(), FakeStore()
    add_tester.main(["judge1", "judge1@example.com"], cognito, store)
    assert cognito.calls == [] and store.staffers == {}


def test_add_tester_apply_makes_user_group_and_active_staffer_without_a_password():
    cognito, store = FakeAdminCognito(), FakeStore()
    add_tester.main(["judge1", "judge1@example.com", "--apply"], cognito, store)
    (_, create), (_, group) = cognito.calls
    assert create["Username"] == "judge1" and "TemporaryPassword" not in create
    assert create["DesiredDeliveryMediums"] == ["EMAIL"]
    assert group["GroupName"] == "staffer"
    staffer = store.staffers["judge1"]
    assert staffer.active and staffer.office == "rep-x" and staffer.role == "staffer"


def test_add_tester_gives_the_office_role_once_the_contracts_have_it(monkeypatch):
    # Lane G's role forbids deny every tool to a staffer with no officeRole, so a judge must get one.
    from typing import Literal

    import mandate_contracts.ddb as ddb

    class StafferWithRole(ddb.StafferItem):
        office_role: Literal["chief_of_staff", "caseworker", "intern"] | None = None

    monkeypatch.setattr(ddb, "StafferItem", StafferWithRole)
    store = FakeStore()
    add_tester.main(["judge1", "judge1@example.com", "--apply"], FakeAdminCognito(), store)
    assert store.staffers["judge1"].office_role == "caseworker"
    add_tester.main(["judge2", "j2@example.com", "--office-role", "intern", "--apply"], FakeAdminCognito(), store)
    assert store.staffers["judge2"].office_role == "intern"
    cognito = FakeAdminCognito()
    with pytest.raises(ValueError):
        add_tester.main(["judge3", "j3@example.com", "--office-role", "mayor", "--apply"], cognito, store)
    assert "judge3" not in store.staffers and cognito.calls == []  # no half-made account


def test_add_tester_rejects_odd_usernames():
    with pytest.raises(SystemExit):
        add_tester.main(["Judge 1", "j@example.com"], FakeAdminCognito(), FakeStore())


# ---------------------------------------------------------------- shell scripts, with a fake aws CLI

WRITE_WORDS = ("deploy", "create-", "put-", "update-", "delete-", "s3 sync", "s3 cp", "s3 rm", "push", "build", "login", "npm")


def fake_bin(tmp_path: Path, stacks_exist: bool) -> tuple[Path, Path]:
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    outputs = json.dumps({
        "RepositoryUri": "123.dkr.ecr.us-east-1.amazonaws.com/homefront-gateway",
        "SecretArn": "arn:aws:secretsmanager:us-east-1:123:secret:homefront/runtime-abc",
        "ConsoleUrl": "https://d111.cloudfront.net/", "ConsoleBucketName": "bucket-1",
        "DistributionId": "E1", "ClusterName": "homefront", "ServiceName": "homefront-gateway",
    })
    aws = f"""#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
open({str(log)!r}, "a").write("aws " + " ".join(args) + "\\n")
cmd = " ".join(args[:2])
if cmd == "sts get-caller-identity": print("123")
elif cmd == "ec2 describe-vpcs": print("vpc-1")
elif cmd == "ec2 describe-subnets": print("subnet-a\\tsubnet-b")
elif cmd == "ec2 describe-managed-prefix-lists": print("pl-3b927c52")
elif cmd == "cloudformation describe-stacks":
    if not {stacks_exist!r}: sys.exit(255)
    query = args[args.index("--query") + 1]
    key = query.split("OutputKey=='")[1].split("'")[0]
    print(json.loads({outputs!r}).get(key, ""))
"""
    recorder = f"""#!/bin/sh
echo "$(basename "$0") $*" >> {str(log)!r}
"""
    for name, body in (("aws", aws), ("docker", recorder), ("npm", recorder), ("fakepy", recorder)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return bin_dir, log


def run_script(tmp_path: Path, script: str, *args: str, stacks_exist: bool) -> tuple[str, list[str]]:
    bin_dir, log = fake_bin(tmp_path, stacks_exist)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PY": str(bin_dir / "fakepy")}
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        env.pop(key, None)
    result = subprocess.run(["bash", str(HERE / script), *args], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    return result.stdout, calls


def writes(calls: list[str]) -> list[str]:
    risky = []
    for call in calls:
        if call.startswith("fakepy"):
            if "--apply" in call:
                risky.append(call)
            continue
        if call.startswith(("docker", "npm")) or any(word in call for word in WRITE_WORDS):
            risky.append(call)
    return risky


@pytest.mark.parametrize("stacks_exist", [False, True])
def test_deploy_dry_run_makes_no_write_call(tmp_path, stacks_exist):
    out, calls = run_script(tmp_path, "deploy.sh", stacks_exist=stacks_exist)
    assert writes(calls) == [], calls
    assert "DRY-RUN: aws cloudformation deploy --stack-name homefront-foundation" in out
    assert "DRY-RUN: docker push" in out
    assert "dry run, nothing changed" in out
    if stacks_exist:  # the helpers ran, in dry-run mode
        assert any(c.startswith("fakepy") and "put_secrets.py" in c for c in calls)
        assert any(c.startswith("fakepy") and "cognito_urls.py https://d111.cloudfront.net/" in c for c in calls)


@pytest.mark.parametrize("stacks_exist", [False, True])
def test_teardown_dry_run_makes_no_write_call(tmp_path, stacks_exist):
    out, calls = run_script(tmp_path, "teardown.sh", stacks_exist=stacks_exist)
    assert writes(calls) == [], calls
    assert "DRY-RUN: aws cloudformation delete-stack --stack-name homefront-app" in out


def test_teardown_apply_removes_only_what_deploy_created(tmp_path):
    _, calls = run_script(tmp_path, "teardown.sh", "--apply", stacks_exist=True)
    deletes = [c for c in calls if " wait " not in c and ("delete-" in c or " rm " in c or "--remove" in c)]
    assert deletes == [
        f"fakepy {HERE}/cognito_urls.py https://d111.cloudfront.net/ --remove --apply",
        "aws s3 rm s3://bucket-1/ --recursive --only-show-errors",
        "aws cloudformation delete-stack --stack-name homefront-app",
        "aws cloudformation delete-stack --stack-name homefront-foundation",
        "aws secretsmanager delete-secret --secret-id arn:aws:secretsmanager:us-east-1:123:secret:homefront/runtime-abc --force-delete-without-recovery",
    ]
    assert not any(word in c for c in calls for word in ("dynamodb", "cognito-idp", "verifiedpermissions"))
