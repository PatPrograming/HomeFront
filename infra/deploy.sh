#!/usr/bin/env bash
# Deploy (or update) the HomeFront Cognito + DynamoDB stack, then print the
# values to paste into Backend/.env and Frontend/.env.
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

POOL="$(out UserPoolId)"
CLIENT="$(out UserPoolClientId)"
POLICY="$(out BackendDataPolicyArn)"

cat <<EOF

Stack $STACK is ready.

# Backend/.env
AWS_REGION=$REGION
COGNITO_USER_POOL_ID=$POOL
COGNITO_APP_CLIENT_ID=$CLIENT
DYNAMODB_TABLE_PREFIX=$PREFIX
AGENT_STORE=dynamodb

# Frontend/.env
VITE_COGNITO_USER_POOL_ID=$POOL
VITE_COGNITO_CLIENT_ID=$CLIENT

Attach this policy to the identity the backend runs as:
  $POLICY
e.g. aws iam attach-user-policy --user-name <you> --policy-arn $POLICY
EOF
