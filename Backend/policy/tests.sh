#!/usr/bin/env bash
# Ask the real Verified Permissions policy store six questions and check each answer.
#
#   own-office case -> ALLOW      other-office case -> DENY     delegator inactive -> DENY
#   bulk export     -> DENY       email to unknown  -> DENY     campaign post      -> DENY
#
# Usage: bash Backend/policy/tests.sh [--dry-run]
#   Reads AVP_POLICY_STORE_ID from Backend/infra/.env.aws (written by setup.sh).
#   --dry-run prints the AWS commands and calls nothing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGION="us-east-1"
ENV_FILE="$ROOT/Backend/infra/.env.aws"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3
export PYTHONPATH="$ROOT/packages/mandate-contracts/src${PYTHONPATH:+:$PYTHONPATH}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

STORE_ID="<policy-store-id>"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  STORE_ID="$AVP_POLICY_STORE_ID"
elif [[ $DRY_RUN == 0 ]]; then
  echo "Missing $ENV_FILE: run Backend/infra/setup.sh first." >&2
  exit 1
fi

failures=0
while read -r case expected; do
  input="$("$PY" "$ROOT/Backend/policy/request.py" "$case" "$STORE_ID")"
  if [[ $DRY_RUN == 1 ]]; then
    echo "+ aws verifiedpermissions is-authorized --region $REGION --cli-input-json '$input'"
    continue
  fi
  actual="$(aws verifiedpermissions is-authorized --region "$REGION" --cli-input-json "$input" --query decision --output text)"
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS  $case: $actual"
  else
    echo "FAIL  $case: expected $expected, got $actual"
    failures=$((failures + 1))
  fi
done < <("$PY" "$ROOT/Backend/policy/request.py" --list)

[[ $DRY_RUN == 1 ]] && exit 0
echo "$failures failure(s)"
[[ $failures == 0 ]]
