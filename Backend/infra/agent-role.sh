#!/usr/bin/env bash
# Create the chat agent's own IAM role, `homefront-agent`: Bedrock only (the Claude model and the guardrail).
# The gateway assumes it per conversation when HOMEFRONT_AGENT_ROLE_ARN is set, so the agent process never holds
# the server's credentials and can't reach DynamoDB, Verified Permissions or KMS around the gateway.
#
# Who may assume it: the deployed gateway's task role (`homefront-gateway-task`, created by Lane L's stack) and,
# with --trust-principal, one more role ARN (your laptop's: run `aws sts get-caller-identity`; for an assumed role
# `arn:aws:sts::<acct>:assumed-role/X/session`, pass `arn:aws:iam::<acct>:role/X`).
#
# Usage: bash Backend/infra/agent-role.sh [--trust-principal <role ARN>]            # dry run: prints, calls nothing
#        bash Backend/infra/agent-role.sh --apply [--trust-principal <role ARN>]    # creates or updates (ask Huy first)
# Then set HOMEFRONT_AGENT_ROLE_ARN=arn:aws:iam::<acct>:role/homefront-agent in the gateway's environment.
set -euo pipefail

REGION="us-east-1"
ROLE="homefront-agent"
GATEWAY_ROLE="homefront-gateway-task"
GUARDRAIL_ID="${GUARDRAIL_ID:-l74ju5pcrcdg}"
DRY_RUN=1
TRUST_PRINCIPAL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) DRY_RUN=0 ;;
    --trust-principal) TRUST_PRINCIPAL="${2:?--trust-principal needs a role ARN}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
if [[ -n "$TRUST_PRINCIPAL" && "$TRUST_PRINCIPAL" != arn:aws:iam::*:role/* ]]; then
  echo "--trust-principal must be a role ARN (arn:aws:iam::<acct>:role/<name>), not an assumed-role session" >&2
  exit 2
fi

if [[ $DRY_RUN == 1 ]]; then
  ACCOUNT="${AWS_ACCOUNT_ID:-<acct>}"  # a dry run calls nothing, not even sts get-caller-identity
else
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
fi

PRINCIPALS="\"arn:aws:iam::${ACCOUNT}:role/${GATEWAY_ROLE}\""
[[ -n "$TRUST_PRINCIPAL" ]] && PRINCIPALS="${PRINCIPALS}, \"${TRUST_PRINCIPAL}\""

# Trust the account root, narrowed to named roles: a direct role principal fails while that role doesn't exist yet.
TRUST_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::${ACCOUNT}:root"},
    "Action": "sts:AssumeRole",
    "Condition": {"ArnEquals": {"aws:PrincipalArn": [${PRINCIPALS}]}}
  }]
}
JSON
)

PERMISSIONS=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ClaudeModels",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/us.anthropic.claude-sonnet-5*",
        "arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/us.anthropic.claude-haiku-5-5*",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5*",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-5-5*"
      ]
    },
    {
      "Sid": "Guardrail",
      "Effect": "Allow",
      "Action": ["bedrock:ApplyGuardrail"],
      "Resource": ["arn:aws:bedrock:${REGION}:${ACCOUNT}:guardrail/${GUARDRAIL_ID}"]
    }
  ]
}
JSON
)

if [[ $DRY_RUN == 1 ]]; then
  echo "Dry run (nothing is created). With --apply this would create or update role ${ROLE}:"
  echo "--- trust policy ---"; echo "$TRUST_POLICY"
  echo "--- permissions (inline policy ${ROLE}-bedrock) ---"; echo "$PERMISSIONS"
  echo "+ aws iam create-role --role-name ${ROLE} --path / --max-session-duration 3600 --tags Key=project,Value=homefront ..."
  echo "+ aws iam put-role-policy --role-name ${ROLE} --policy-name ${ROLE}-bedrock ..."
  echo "Then: HOMEFRONT_AGENT_ROLE_ARN=arn:aws:iam::${ACCOUNT}:role/${ROLE}"
  exit 0
fi

if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "Role ${ROLE} exists: updating its trust policy"
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST_POLICY"
  aws iam update-role --role-name "$ROLE" --max-session-duration 3600
  aws iam tag-role --role-name "$ROLE" --tags Key=project,Value=homefront  # a role made elsewhere gets the tag too
else
  aws iam create-role --role-name "$ROLE" --path / --max-session-duration 3600 \
    --description "HomeFront chat agent: Bedrock only (assumed by the gateway per conversation)" \
    --assume-role-policy-document "$TRUST_POLICY" --tags Key=project,Value=homefront >/dev/null
  echo "Created role ${ROLE}"
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name "${ROLE}-bedrock" --policy-document "$PERMISSIONS"
echo "Done. Set HOMEFRONT_AGENT_ROLE_ARN=arn:aws:iam::${ACCOUNT}:role/${ROLE} for the gateway."
