"""Fill the homefront/runtime secret in Secrets Manager. Dry run unless --apply.

    python deploy/aws/put_secrets.py [--env-file .env] [--apply]

- MANDATE_SIGNING_SECRET: kept as CloudFormation generated it (it never leaves AWS).
- COGNITO_AGENT_CLIENT_SECRET: read from the Cognito agent app client (describe-user-pool-client).
- Government API keys: read from --env-file (your repo-root .env) when present there.

Every key the task definition maps is written, empty when you have no value, because ECS refuses
to start a task whose secret is missing a key. Prints key names and set/empty only, never a value.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REGION = "us-east-1"
SECRET_ID = "homefront/runtime"
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "us-east-1_MLfQpodxG")
AGENT_CLIENT_ID = os.environ.get("COGNITO_AGENT_CLIENT_ID", "7aslk40jv9ikchoouqd00avdnp")

# Must match the Secrets list in deploy/aws/app.yaml.
GOV_KEYS = (
    "DATA_GOV_API_KEY", "CENSUS_API_KEY", "OPENFDA_API_KEY", "EIA_API_KEY", "NREL_API_KEY",
    "NASA_API_KEY", "NOAA_CDO_TOKEN", "NPS_API_KEY", "BLS_API_KEY", "EPA_API_KEY",
    "FEC_API_KEY", "FCC_API_KEY",
)
SECRET_KEYS = ("MANDATE_SIGNING_SECRET", "COGNITO_AGENT_CLIENT_SECRET", *GOV_KEYS)


def read_env_file(path: Path, wanted: tuple[str, ...]) -> dict[str, str]:
    """KEY=VALUE lines for the wanted keys only; quotes stripped. Missing file = nothing."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key in wanted and value:
            values[key] = value
    return values


def merged_secret(current: dict[str, str], agent_secret: str, gov: dict[str, str]) -> dict[str, str]:
    if not current.get("MANDATE_SIGNING_SECRET"):
        raise SystemExit("the secret has no MANDATE_SIGNING_SECRET; deploy the foundation stack first")
    merged = {key: "" for key in SECRET_KEYS}
    merged.update({k: v for k, v in current.items() if k in SECRET_KEYS})
    merged.update(gov)
    merged["COGNITO_AGENT_CLIENT_SECRET"] = agent_secret
    return merged


def summary(secret: dict[str, str]) -> str:
    return "\n".join(f"  {key}: {'set' if secret.get(key) else 'empty'}" for key in SECRET_KEYS)


def main(argv: list[str] | None = None, cognito=None, secrets=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
    parser.add_argument("--apply", action="store_true", help="write the secret (default: dry run)")
    args = parser.parse_args(argv)

    if cognito is None or secrets is None:
        import boto3

        cognito = cognito or boto3.client("cognito-idp", region_name=REGION)
        secrets = secrets or boto3.client("secretsmanager", region_name=REGION)

    agent = cognito.describe_user_pool_client(UserPoolId=USER_POOL_ID, ClientId=AGENT_CLIENT_ID)
    agent_secret = agent["UserPoolClient"].get("ClientSecret", "")
    if not agent_secret:
        raise SystemExit("the agent app client has no secret; check Backend/infra/cognito.sh")
    current = json.loads(secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"])
    secret = merged_secret(current, agent_secret, read_env_file(Path(args.env_file), GOV_KEYS))

    print(f"{SECRET_ID} will hold:\n{summary(secret)}")
    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply to write the secret.")
        return 0
    secrets.put_secret_value(SecretId=SECRET_ID, SecretString=json.dumps(secret))
    print(f"wrote {SECRET_ID}. A running task picks it up on its next deploy (deploy.sh --apply).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
