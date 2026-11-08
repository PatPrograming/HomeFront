#!/usr/bin/env bash
# Deploy HomeFront to AWS (us-east-1). DRY RUN unless --apply: without it, every command that
# would create or change something is printed instead of run. Read-only lookups always run.
#
#   deploy/aws/deploy.sh            # show the plan
#   deploy/aws/deploy.sh --apply    # do it (about 15 minutes the first time; CloudFront is slow)
#
# Steps: foundation stack (ECR + secret) -> build and push the image -> fill the secret ->
# app stack (ECS, ALB, CloudFront, S3) -> build the console and upload it -> add the HTTPS URL to
# the Cognito staff-web client -> smoke test. Safe to re-run: each step updates in place.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/deploy/aws"
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
FOUNDATION_STACK="${FOUNDATION_STACK:-homefront-foundation}"
APP_STACK="${APP_STACK:-homefront-app}"
PY="${PY:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY=/usr/local/bin/python3.12

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
APPLY_FLAG=""  # passed unquoted to the Python helpers; empty means dry run
(( APPLY )) && APPLY_FLAG="--apply"

run() {  # a command that changes something
  if (( APPLY )); then "$@"; else printf 'DRY-RUN: %s\n' "$*"; fi
}
stack_output() {  # read-only; empty when the stack doesn't exist yet
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null || true
}
step() { printf '\n== %s\n' "$*"; }

step "0. Who and where"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
echo "account $ACCOUNT, region $AWS_REGION, mode $( (( APPLY )) && echo APPLY || echo DRY-RUN )"

step "1. Foundation stack $FOUNDATION_STACK (ECR repository + homefront/runtime secret)"
run aws cloudformation deploy --stack-name "$FOUNDATION_STACK" \
  --template-file "$HERE/foundation.yaml" --no-fail-on-empty-changeset
REPO_URI="$(stack_output "$FOUNDATION_STACK" RepositoryUri)"
SECRET_ARN="$(stack_output "$FOUNDATION_STACK" SecretArn)"
REPO_URI="${REPO_URI:-$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/homefront-gateway}"
SECRET_ARN="${SECRET_ARN:-<SecretArn from $FOUNDATION_STACK>}"

step "2. Build and push the gateway image (linux/arm64)"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet HEAD -- Backend packages agent deploy/aws || echo -dirty)"
IMAGE="$REPO_URI:$TAG"
run docker build --platform linux/arm64 -f "$HERE/Dockerfile" -t "$IMAGE" "$ROOT"
if (( APPLY )); then
  aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO_URI%%/*}" >/dev/null
else
  echo "DRY-RUN: aws ecr get-login-password | docker login ... ${REPO_URI%%/*}"
fi
run docker push "$IMAGE"

step "3. Secrets (key names only are printed)"
if (( APPLY )) || [[ -n "$(stack_output "$FOUNDATION_STACK" SecretArn)" ]]; then
  "$PY" "$HERE/put_secrets.py" $APPLY_FLAG
else
  echo "DRY-RUN: $PY deploy/aws/put_secrets.py --apply   (the secret doesn't exist yet)"
fi

step "4. App stack $APP_STACK (ECS Fargate, internal ALB, CloudFront, S3)"
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
SUBNETS="$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')"
PREFIX_LIST="$(aws ec2 describe-managed-prefix-lists \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text)"
echo "default VPC $VPC_ID, subnets $SUBNETS, CloudFront prefix list $PREFIX_LIST"
PARAMS=(ImageUri="$IMAGE" SecretArn="$SECRET_ARN" VpcId="$VPC_ID" SubnetIds="$SUBNETS"
        CloudFrontPrefixListId="$PREFIX_LIST")
[[ -n "${SES_FROM:-}" ]] && PARAMS+=(SesFromAddress="$SES_FROM")
[[ -n "${AVP_POLICY_NAMES:-}" ]] && PARAMS+=(AvpPolicyNames="$AVP_POLICY_NAMES")
# Only for a redeploy in another account (README, "If the event account is wiped"); the template's
# defaults are this event's IDs.
[[ -n "${COGNITO_USER_POOL_ID:-}" ]] && PARAMS+=(CognitoUserPoolId="$COGNITO_USER_POOL_ID")
[[ -n "${COGNITO_STAFF_CLIENT_ID:-}" ]] && PARAMS+=(CognitoStaffClientId="$COGNITO_STAFF_CLIENT_ID")
[[ -n "${COGNITO_AGENT_CLIENT_ID:-}" ]] && PARAMS+=(CognitoAgentClientId="$COGNITO_AGENT_CLIENT_ID")
[[ -n "${COGNITO_DOMAIN:-}" ]] && PARAMS+=(CognitoDomain="$COGNITO_DOMAIN")
[[ -n "${AVP_POLICY_STORE_ID:-}" ]] && PARAMS+=(PolicyStoreId="$AVP_POLICY_STORE_ID")
[[ -n "${GUARDRAIL_ID:-}" ]] && PARAMS+=(GuardrailId="$GUARDRAIL_ID")
[[ -n "${GUARDRAIL_VERSION:-}" ]] && PARAMS+=(GuardrailVersion="$GUARDRAIL_VERSION")
run aws cloudformation deploy --stack-name "$APP_STACK" --template-file "$HERE/app.yaml" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset --parameter-overrides "${PARAMS[@]}"
# A re-run with the same image tag would not restart the task; force it so new secrets load.
CLUSTER="$(stack_output "$APP_STACK" ClusterName)"
SERVICE="$(stack_output "$APP_STACK" ServiceName)"
if [[ -n "$CLUSTER" && -n "$SERVICE" ]]; then
  run aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment --query service.serviceName --output text
fi

URL="$(stack_output "$APP_STACK" ConsoleUrl)"
BUCKET="$(stack_output "$APP_STACK" ConsoleBucketName)"
DIST_ID="$(stack_output "$APP_STACK" DistributionId)"
URL="${URL:-https://<distribution>.cloudfront.net/}"

step "5. Console: Vite build and upload to S3"
# Public values only (they reach the browser). Process env beats any repo-root .env, and mock mode stays off.
(
  cd "$ROOT/Frontend"
  export VITE_API_BASE=/api/v1 VITE_MOCK_API=0 \
         VITE_COGNITO_DOMAIN="${COGNITO_DOMAIN:-https://mandate-a10-hack-2026.auth.us-east-1.amazoncognito.com}" \
         VITE_COGNITO_CLIENT_ID="${COGNITO_STAFF_CLIENT_ID:-5be15oitnpm14bg188jpqqs9qv}"
  run npm ci --no-audit --no-fund
  run npm run build
)
if [[ -n "$BUCKET" ]] || (( APPLY )); then
  BUCKET="${BUCKET:-$(stack_output "$APP_STACK" ConsoleBucketName)}"
  run aws s3 sync "$ROOT/Frontend/dist/" "s3://$BUCKET/" --delete --exclude index.html \
    --cache-control "public,max-age=31536000,immutable"
  run aws s3 cp "$ROOT/Frontend/dist/index.html" "s3://$BUCKET/index.html" --cache-control "no-cache"
  run aws cloudfront create-invalidation --distribution-id "${DIST_ID}" --paths "/*" --query Invalidation.Id --output text
else
  echo "DRY-RUN: aws s3 sync Frontend/dist/ s3://<ConsoleBucketName>/ ; aws cloudfront create-invalidation --paths '/*'"
fi

step "6. Cognito staff-web client: add $URL to callback and logout URLs (localhost kept)"
if [[ "$URL" == https://*.cloudfront.net/ ]]; then
  "$PY" "$HERE/cognito_urls.py" "$URL" $APPLY_FLAG
else
  echo "DRY-RUN: $PY deploy/aws/cognito_urls.py <ConsoleUrl> --apply"
fi

step "7. Smoke test"
if (( APPLY )); then
  "$HERE/smoke.sh" "$URL"
else
  echo "DRY-RUN: deploy/aws/smoke.sh $URL"
fi

printf '\nDone (%s). Console: %s\n' "$( (( APPLY )) && echo applied || echo dry run, nothing changed )" "$URL"
