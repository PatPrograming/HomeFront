#!/usr/bin/env bash
# Remove what deploy.sh created. DRY RUN unless --apply.
#   deploy/aws/teardown.sh            # show what would be removed
#   deploy/aws/teardown.sh --apply    # remove it (CloudFront takes 5-15 minutes)
#
# Removes: the CloudFront URL from the Cognito staff-web client, the console files, the app stack
# (ECS, ALB, CloudFront, S3 bucket, roles, log group) and the foundation stack (ECR images, secret).
# Never touches what existed before: the Cognito pool, the mandate and homefront-* tables, the
# Verified Permissions store, the guardrail.
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
APPLY_FLAG=""
(( APPLY )) && APPLY_FLAG="--apply"

run() { if (( APPLY )); then "$@"; else printf 'DRY-RUN: %s\n' "$*"; fi; }
stack_output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null || true
}

URL="$(stack_output "$APP_STACK" ConsoleUrl)"
BUCKET="$(stack_output "$APP_STACK" ConsoleBucketName)"
SECRET_ARN="$(stack_output "$FOUNDATION_STACK" SecretArn)"

echo "== 1. Cognito staff-web: remove ${URL:-<no app stack>} (localhost stays)"
if [[ -n "$URL" ]]; then "$PY" "$HERE/cognito_urls.py" "$URL" --remove $APPLY_FLAG; fi

echo "== 2. Empty the console bucket ${BUCKET:-<none>} (CloudFormation can't delete a non-empty bucket)"
if [[ -n "$BUCKET" ]]; then run aws s3 rm "s3://$BUCKET/" --recursive --only-show-errors; fi

echo "== 3. Delete stack $APP_STACK"
run aws cloudformation delete-stack --stack-name "$APP_STACK"
run aws cloudformation wait stack-delete-complete --stack-name "$APP_STACK"

echo "== 4. Delete stack $FOUNDATION_STACK (ECR repository with its images, the secret)"
run aws cloudformation delete-stack --stack-name "$FOUNDATION_STACK"
run aws cloudformation wait stack-delete-complete --stack-name "$FOUNDATION_STACK"
# CloudFormation schedules the secret for deletion after a recovery window; free the name now
# so a later deploy.sh can recreate homefront/runtime.
if [[ -n "$SECRET_ARN" ]]; then
  run aws secretsmanager delete-secret --secret-id "$SECRET_ARN" --force-delete-without-recovery || true
fi

echo "Done ($( (( APPLY )) && echo removed || echo dry run, nothing changed ))."
