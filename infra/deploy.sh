#!/usr/bin/env bash
# Deploy (or update) the HomeFront DynamoDB stack, then print the
# values to paste into the repo-root `.env`.
#
#   AWS_REGION=us-east-1 ./infra/deploy.sh            # stack "homefront"
#   STACK=homefront-dev PREFIX=homefront-dev- ./infra/deploy.sh
#
# Needs the AWS CLI with credentials that can create Cognito, DynamoDB and IAM
# resources.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK="${STACK:-homefront}"
PREFIX="${PREFIX:-homefront-}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file "$ROOT/infra/cloudformation/homefront.yaml" \
  --parameter-overrides "Prefix=$PREFIX" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset

out() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

POLICY="$(out BackendDataPolicyArn)"

# The stack's own Cognito pool is retired (users migrated 2026-11-08, see infra/README.md):
# sign-in always uses the mandate pool below, built by Backend/infra/cognito.sh.
cat <<EOF

Stack $STACK is ready.

# Paste into repo-root .env (Backend + Frontend share this file)
AWS_REGION=$REGION
COGNITO_USER_POOL_ID=us-east-1_MLfQpodxG
COGNITO_STAFF_CLIENT_ID=5be15oitnpm14bg188jpqqs9qv
COGNITO_AGENT_CLIENT_ID=7aslk40jv9ikchoouqd00avdnp
COGNITO_DOMAIN=https://mandate-a10-hack-2026.auth.us-east-1.amazoncognito.com
DYNAMODB_TABLE_PREFIX=$PREFIX
AGENT_STORE=dynamodb

Attach this policy to the identity the backend runs as:
  $POLICY
e.g. aws iam attach-user-policy --user-name <you> --policy-arn $POLICY
EOF
