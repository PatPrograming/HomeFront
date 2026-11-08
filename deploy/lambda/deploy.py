"""Deploy HomeFront to AWS: the API on Lambda (function URL), the chat agent on ECS Fargate.

    .venv/bin/python deploy/lambda/deploy.py
    .venv/bin/python deploy/lambda/deploy.py --apply --update-cognito
    .venv/bin/python deploy/lambda/deploy.py --apply

The first is a dry run: it prints the plan and changes nothing. The second is the first deploy
(also allows the new URL at Cognito sign-in). The third is every later deploy.

Settings come from the repo-root .env, Backend/infra/.env.aws and agent/.env (shell variables win).
They are stored in Secrets Manager (`homefront/config` for the API, `homefront/agent` for the
agent's Cognito secret) and never baked into an image or printed. See deploy/lambda/README.md.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STACK = "homefront"
REPOSITORY = "homefront"
CONFIG_SECRET = "homefront/config"
AGENT_SECRET = "homefront/agent"

# Settings the API reads (all go into homefront/config).
API_KEYS = {
    "BEDROCK_REGION", "BEDROCK_MODEL_ID", "BEDROCK_FALLBACK_MODEL_ID", "RUNNER_EFFORT", "AWS_BEARER_TOKEN_BEDROCK",
    "COGNITO_REGION", "COGNITO_USER_POOL_ID", "COGNITO_STAFF_CLIENT_ID", "COGNITO_AGENT_CLIENT_ID", "COGNITO_DOMAIN",
    "MANDATE_SIGNING_SECRET", "AAUTH_SIGNING_KEY", "MANDATE_TABLE", "AVP_POLICY_STORE_ID", "AVP_POLICY_NAMES",
    "GUARDRAIL_ID", "GUARDRAIL_VERSION", "DYNAMODB_TABLE_PREFIX", "AGENT_STORE", "NOAA_CDO_TOKEN",
}
API_PATTERNS = (re.compile(r"^HOMEFRONT_"), re.compile(r"_API_KEY$"))
# Set by Lambda or by the stack; never copied from a laptop.
NEVER = {"AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
         "AWS_PROFILE", "COGNITO_AGENT_CLIENT_SECRET", "HOMEFRONT_AGENT_LAUNCHER", "HOMEFRONT_RUNTIME_TABLE",
         "HOMEFRONT_CONFIG_SECRET", "HOMEFRONT_CONFIG_VERSION", "ALLOWED_ORIGINS"}
SECRET_HINT = re.compile(r"(SECRET|TOKEN|_KEY)$")
REQUIRED = ("MANDATE_SIGNING_SECRET", "AVP_POLICY_STORE_ID", "COGNITO_USER_POOL_ID",
            "COGNITO_STAFF_CLIENT_ID", "COGNITO_AGENT_CLIENT_ID", "COGNITO_DOMAIN")


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, same rules as scripts/run-gateway.sh (comments skipped, outer quotes stripped)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            values[key] = value
    return values


def wanted(key: str) -> bool:
    return key not in NEVER and (key in API_KEYS or any(p.search(key) for p in API_PATTERNS))


def collect() -> tuple[dict[str, str], dict[str, str], list[str]]:
    """(api config, agent settings, problems)."""
    files = read_env_file(ROOT / ".env") | read_env_file(ROOT / "Backend" / "infra" / ".env.aws")
    agent_file = read_env_file(ROOT / "agent" / ".env")
    shell = {k: v for k, v in os.environ.items() if v}

    config = {k: v for k, v in files.items() if wanted(k)} | {k: v for k, v in shell.items() if wanted(k)}
    config.setdefault("AGENT_STORE", "dynamodb")  # the console's Agents/Approvals/Activity tabs
    if not config.get("HOMEFRONT_LIVE_DATAGOV"):
        config["HOMEFRONT_LIVE_DATAGOV"] = "1" if config.get("DATA_GOV_API_KEY") else "0"

    def pick(key: str, default: str = "") -> str:
        return shell.get(key) or files.get(key) or agent_file.get(key) or default

    agent = {
        "secret": shell.get("COGNITO_AGENT_CLIENT_SECRET") or files.get("COGNITO_AGENT_CLIENT_SECRET")
        or agent_file.get("COGNITO_AGENT_CLIENT_SECRET", ""),
        # agent/.env's BEDROCK_MODEL_ID is the agent's model; the API's BEDROCK_MODEL_ID is the task runner's.
        "model": shell.get("AGENT_BEDROCK_MODEL_ID") or agent_file.get("BEDROCK_MODEL_ID") or "us.anthropic.claude-sonnet-5",
        "region": pick("AWS_REGION", "us-east-1"),
    }
    config.setdefault("HOMEFRONT_AUTOSTART_AGENT", "1" if agent["secret"] else "0")

    problems = [f"{key} is not set" for key in REQUIRED if not config.get(key)]
    if 0 < len(config.get("MANDATE_SIGNING_SECRET", "")) < 32:
        problems.append("MANDATE_SIGNING_SECRET must be at least 32 characters")
    if config["HOMEFRONT_AUTOSTART_AGENT"] in {"1", "true", "yes", "on"} and not agent["secret"]:
        problems.append("HOMEFRONT_AUTOSTART_AGENT=1 needs COGNITO_AGENT_CLIENT_SECRET (in .env or agent/.env)")
    return config, agent, problems


def run(cmd: list[str], **kwargs) -> None:
    shown = cmd[:4] + ["…"] if "--password-stdin" in cmd else cmd
    print("  $", " ".join(shown))
    subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def image_tag() -> str:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return f"{sha or 'local'}-{time.strftime('%Y%m%d%H%M%S')}"


def ensure_repository(region: str) -> str:
    import boto3

    ecr = boto3.client("ecr", region_name=region)
    try:
        repo = ecr.describe_repositories(repositoryNames=[REPOSITORY])["repositories"][0]
    except ecr.exceptions.RepositoryNotFoundException:
        repo = ecr.create_repository(repositoryName=REPOSITORY, imageScanningConfiguration={"scanOnPush": True})["repository"]
        ecr.put_lifecycle_policy(repositoryName=REPOSITORY, lifecyclePolicyText=json.dumps({"rules": [{
            "rulePriority": 1, "description": "keep the last 20 images",
            "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 20},
            "action": {"type": "expire"}}]}))
        print(f"  created ECR repository {REPOSITORY}")
    return repo["repositoryUri"]


def build_and_push(region: str, repo_uri: str, vite: dict[str, str]) -> tuple[str, str]:
    import boto3

    tag = image_tag()
    token = boto3.client("ecr", region_name=region).get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
    run(["docker", "login", "--username", user, "--password-stdin", token["proxyEndpoint"]],
        input=password.encode(), stdout=subprocess.DEVNULL)
    images = {}
    for target in ("api", "agent"):
        image = f"{repo_uri}:{target}-{tag}"
        build_args = [a for k, v in vite.items() for a in ("--build-arg", f"{k}={v}")] if target == "api" else []
        # One plain arm64 manifest: Lambda refuses image indexes (what attestations produce).
        run(["docker", "buildx", "build", "--platform", "linux/arm64", "--target", target,
             "--provenance=false", "--sbom=false",
             "--output", f"type=image,name={image},push=true,oci-mediatypes=false", *build_args, "."])
        images[target] = image
    return images["api"], images["agent"]


def put_secret(region: str, name: str, value: str) -> str:
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)
    try:
        arn = sm.put_secret_value(SecretId=name, SecretString=value)["ARN"]
        print(f"  updated {name}")
    except sm.exceptions.ResourceNotFoundException:
        arn = sm.create_secret(Name=name, SecretString=value, Description="HomeFront deploy (deploy/lambda)")["ARN"]
        print(f"  created {name}")
    return arn


def default_network(region: str) -> tuple[str, list[str]]:
    import boto3

    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("No default VPC in this region: set AGENT_VPC_ID and AGENT_SUBNET_IDS (public subnets).")
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]},
                                            {"Name": "default-for-az", "Values": ["true"]}])["Subnets"]
    return vpcs[0]["VpcId"], sorted(s["SubnetId"] for s in subnets)


def deploy_stack(region: str, params: dict[str, str]) -> dict[str, str]:
    import boto3

    run(["aws", "cloudformation", "deploy", "--region", region, "--stack-name", STACK,
         "--template-file", str(HERE / "stack.yaml"), "--capabilities", "CAPABILITY_IAM",
         "--no-fail-on-empty-changeset", "--parameter-overrides", *[f"{k}={v}" for k, v in params.items()]])
    stack = boto3.client("cloudformation", region_name=region).describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def wait_healthy(url: str) -> None:
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"{url}/api/v1/health", timeout=30) as response:
                if response.status == 200:
                    print("  healthy")
                    return
        except Exception as error:  # cold start, or the function failing to start
            last = error
        time.sleep(5)
    sys.exit(f"  {url}/api/v1/health did not answer ({last}). Logs: CloudWatch log group /aws/lambda/homefront-api")


def allow_url_in_cognito(region: str, pool_id: str, client_id: str, url: str) -> None:
    """Add `url/` to the staff-web client's callback and logout URLs, keeping every other setting."""
    import boto3

    cognito = boto3.client("cognito-idp", region_name=region)
    current = cognito.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]
    target = url.rstrip("/") + "/"
    callbacks, logouts = current.get("CallbackURLs", []), current.get("LogoutURLs", [])
    print(f"  client {current['ClientName']}: allow {target}")
    if target in callbacks and target in logouts:
        print("  already allowed")
        return
    # UpdateUserPoolClient resets anything left out, so send back every field it accepts.
    accepted = cognito.meta.service_model.operation_model("UpdateUserPoolClient").input_shape.members
    update = {k: v for k, v in current.items() if k in accepted}
    update["CallbackURLs"] = sorted(set(callbacks) | {target})
    update["LogoutURLs"] = sorted(set(logouts) | {target})
    cognito.update_user_pool_client(**update)
    print("  done")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="change AWS (default: dry run)")
    parser.add_argument("--update-cognito", action="store_true", help="add the URL to the staff-web client's allowed URLs")
    args = parser.parse_args()

    config, agent, problems = collect()
    region = agent["region"]
    env_file = read_env_file(ROOT / ".env")
    vite = {k: os.environ.get(k) or env_file.get(k, "") for k in ("VITE_COGNITO_DOMAIN", "VITE_COGNITO_CLIENT_ID")}

    print(f"Region {region}. API settings ({CONFIG_SECRET}; secret values hidden):")
    for key in sorted(config):
        print(f"  {key}={'<hidden>' if SECRET_HINT.search(key) else config[key]}")
    print(f"Agent ({AGENT_SECRET}): client secret {'set' if agent['secret'] else 'NOT set'}, model {agent['model']}")
    print("Console build:", ", ".join(f"{k}={v or '(empty)'}" for k, v in vite.items()))
    if problems:
        sys.exit("\nCannot deploy:\n  - " + "\n  - ".join(problems))
    if not args.apply:
        print("\nDry run. Nothing changed. Re-run with --apply to deploy.")
        return

    import boto3

    identity = boto3.client("sts", region_name=region).get_caller_identity()
    print(f"\nDeploying to account {identity['Account']} as {identity['Arn']}")

    print("\n1. Images (arm64)")
    api_image, agent_image = build_and_push(region, ensure_repository(region), vite)

    print("\n2. Secrets Manager")
    config_json = json.dumps(config, sort_keys=True)
    config_arn = put_secret(region, CONFIG_SECRET, config_json)
    agent_arn = put_secret(region, AGENT_SECRET, agent["secret"]) if agent["secret"] else ""

    print("\n3. CloudFormation stack", STACK, "(~3 min the first time)")
    vpc_id, subnets = os.environ.get("AGENT_VPC_ID"), os.environ.get("AGENT_SUBNET_IDS")
    if not (vpc_id and subnets):
        vpc_id, subnet_list = default_network(region)
        subnets = ",".join(subnet_list)
    outputs = deploy_stack(region, {
        "ApiImageUri": api_image,
        "AgentImageUri": agent_image,
        "ConfigSecretArn": config_arn,
        "ConfigVersion": hashlib.sha256(config_json.encode()).hexdigest()[:16],
        "AgentSecretArn": agent_arn,
        "PolicyStoreId": config["AVP_POLICY_STORE_ID"],
        "MandateTable": config.get("MANDATE_TABLE", "mandate"),
        "TablePrefix": config.get("DYNAMODB_TABLE_PREFIX", "homefront-"),
        "VpcId": vpc_id,
        "SubnetIds": subnets,
        "CognitoDomain": config["COGNITO_DOMAIN"],
        "AgentClientId": config["COGNITO_AGENT_CLIENT_ID"],
        "AgentModelId": agent["model"],
        "GuardrailId": config.get("GUARDRAIL_ID", ""),
        "GuardrailVersion": config.get("GUARDRAIL_VERSION", ""),
    })
    url = outputs["Url"].rstrip("/")

    print("\n4. Health check")
    wait_healthy(url)
    if args.update_cognito:
        print("\n5. Cognito sign-in")
        allow_url_in_cognito(config.get("COGNITO_REGION", region), config["COGNITO_USER_POOL_ID"],
                             config["COGNITO_STAFF_CLIENT_ID"], url)

    print(f"\nHomeFront is running at {url}")
    print(f"  console: {url}/console   API docs: {url}/docs")
    print("  logs: CloudWatch /aws/lambda/homefront-api (API, runs) and /homefront/agent (chat agents)")
    if not args.update_cognito:
        print("  Sign-in needs this URL allowed at Cognito: re-run with --update-cognito (once).")


if __name__ == "__main__":
    main()
