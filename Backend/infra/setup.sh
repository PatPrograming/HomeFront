#!/usr/bin/env bash
# Create HomeFront's AWS resources in us-east-1. Safe to run again: anything that
# already exists is reused, nothing is deleted.
#
#   1. DynamoDB table `mandate` (pk/sk strings, on-demand)
#   2. Verified Permissions policy store (validation off)
#   3. Every Cedar policy in packages/mandate-contracts/policies/*.cedar (starter policies, office-role forbids, new tool permits; re-runs add only new ones)
#   4. Backend/infra/.env.aws (untracked): store id + map of AVP policy id -> readable name
#
# Usage: bash Backend/infra/setup.sh            # dry run: prints the AWS commands, calls nothing
#        bash Backend/infra/setup.sh --apply    # really creates the resources (ask Huy first)
#        bash Backend/infra/setup.sh --apply --with-role-policies
#            also adds the office-role forbids (policies/role-*.cedar, docs/ROLES.md). It first reads
#            every STAFFER item and stops, adding nothing, if any staffer has no valid officeRole:
#            the role forbids deny every tool to such a staffer. Write the roles with
#            test-users.sh --apply first. The new tool permits (NEEDS_ROLE_POLICIES) are added only with
#            the flag too: alone they would give every role, interns included, those tools.
#            Without the flag, role policies already in the store are kept and still named in
#            .env.aws; none are added.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGION="us-east-1"
TABLE="${MANDATE_TABLE:-mandate}"
STORE_DESCRIPTION="mandate"
POLICY_DIR="$ROOT/packages/mandate-contracts/policies"
ENV_FILE="${MANDATE_ENV_FILE:-$ROOT/Backend/infra/.env.aws}"
# Dry run unless --apply is passed, so a stray run can never create anything.
DRY_RUN=1
WITH_ROLES=0
# Permits that grant a tool to every role; only safe next to the role-*.cedar forbids that narrow them.
NEEDS_ROLE_POLICIES=" research-tools email-send-list-office social-reply-official report-save "
for arg in "$@"; do
  case "$arg" in
    --apply) DRY_RUN=0 ;;
    --with-role-policies) WITH_ROLES=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Run an AWS CLI command, or only print it in dry-run mode.
run() {
  if [[ $DRY_RUN == 1 ]]; then
    echo "+ aws $* --region $REGION" >&2
    return 0
  fi
  aws "$@" --region "$REGION"
}

# Read-only lookups print nothing in dry-run mode (and so look like "not found").
# Any AWS error stops the script, so a failed lookup never leads to a duplicate.
lookup() {
  [[ $DRY_RUN == 1 ]] && return 0
  aws "$@" --region "$REGION"
}

# The one lookup where "not found" is an error from AWS: describe-table.
table_exists() {
  [[ $DRY_RUN == 1 ]] && return 1
  local err
  if err="$(aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" 2>&1 >/dev/null)"; then
    return 0
  fi
  [[ "$err" == *ResourceNotFoundException* ]] && return 1
  echo "$err" >&2
  exit 1
}

echo "== 1. DynamoDB table $TABLE"
if table_exists; then
  echo "exists"
else
  run dynamodb create-table --table-name "$TABLE" \
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
    --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST >/dev/null
  run dynamodb wait table-exists --table-name "$TABLE"
  echo "created"
fi

echo "== 2. Verified Permissions policy store"
STORE_ID="$(lookup verifiedpermissions list-policy-stores \
  --query "policyStores[?description=='$STORE_DESCRIPTION'].policyStoreId | [0]" --output text)"
if [[ -n "$STORE_ID" && "$STORE_ID" != "None" ]]; then
  echo "exists: $STORE_ID"
else
  STORE_ID="$(run verifiedpermissions create-policy-store --validation-settings mode=OFF \
    --description "$STORE_DESCRIPTION" --query policyStoreId --output text)"
  [[ $DRY_RUN == 1 ]] && STORE_ID="<new-policy-store-id>"
  echo "created: $STORE_ID"
fi

if [[ $WITH_ROLES == 1 ]]; then
  echo "== 2b. Every STAFFER item has an office role"
  if [[ $DRY_RUN == 1 ]]; then
    echo "+ aws dynamodb scan --table-name $TABLE (STAFFER#*/META items: pk, officeRole) --region $REGION" >&2
  else
    staffers="$(aws dynamodb scan --table-name "$TABLE" --region "$REGION" --output json \
      --filter-expression "begins_with(pk, :p) AND sk = :m" \
      --expression-attribute-values '{":p": {"S": "STAFFER#"}, ":m": {"S": "META"}}' \
      --projection-expression "pk, officeRole")"
    # Prints the staffers with no valid role; any output stops the run before a role policy exists.
    missing="$(PYTHONPATH="$ROOT/packages/mandate-contracts/src" "${PYTHON:-$ROOT/.venv/bin/python}" -c '
import json, sys
from mandate_contracts.roles import ROLE_TOOLS
for item in json.loads(sys.argv[1]).get("Items", []):
    role = item.get("officeRole", {}).get("S")
    if role not in ROLE_TOOLS:
        print(item["pk"]["S"].removeprefix("STAFFER#"), role or "(none)")
' "$staffers")"
    if [[ -n "$missing" ]]; then
      echo "Stopped: these staffers have no valid office role, and the role policies would deny them everything:" >&2
      echo "$missing" >&2
      echo "Run bash Backend/infra/test-users.sh --apply first (it writes every role), then re-run this." >&2
      exit 1
    fi
    echo "ok"
  fi
fi

echo "== 3. Cedar policies"
NAMES_JSON="{}"
for file in "$POLICY_DIR"/*.cedar; do
  name="$(basename "$file" .cedar)"
  # AVP keeps a static policy's description; we store the readable name there.
  policy_id="$(lookup verifiedpermissions list-policies --policy-store-id "$STORE_ID" \
    --query "policies[?definition.static.description=='$name'].policyId | [0]" --output text)"
  if [[ -n "$policy_id" && "$policy_id" != "None" ]]; then
    echo "exists: $name ($policy_id)"
  elif [[ ( $name == role-* || $NEEDS_ROLE_POLICIES == *" $name "* ) && $WITH_ROLES == 0 ]]; then
    echo "skipped: $name (pass --with-role-policies to add the office-role policies)"
    continue
  else
    definition="$(python3 -c 'import json,sys; print(json.dumps({"static": {"description": sys.argv[1], "statement": open(sys.argv[2]).read()}}))' "$name" "$file")"
    policy_id="$(run verifiedpermissions create-policy --policy-store-id "$STORE_ID" \
      --definition "$definition" --query policyId --output text)"
    [[ $DRY_RUN == 1 ]] && policy_id="<id-of-$name>"
    echo "created: $name ($policy_id)"
  fi
  NAMES_JSON="$(python3 -c 'import json,sys; m=json.loads(sys.argv[1]); m[sys.argv[2]]=sys.argv[3]; print(json.dumps(m))' "$NAMES_JSON" "$policy_id" "$name")"
done

echo "== 4. $ENV_FILE"
ENV_CONTENT="AWS_REGION=$REGION
MANDATE_TABLE=$TABLE
AVP_POLICY_STORE_ID=$STORE_ID
AVP_POLICY_NAMES='$NAMES_JSON'"
if [[ $DRY_RUN == 1 ]]; then
  echo "would write:"
  echo "$ENV_CONTENT"
else
  echo "$ENV_CONTENT" >"$ENV_FILE"
  echo "written (untracked: .gitignore covers .env.*)"
fi
