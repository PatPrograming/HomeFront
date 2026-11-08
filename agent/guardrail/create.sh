#!/usr/bin/env bash
# Create (or update) Mandate's Bedrock guardrail and publish a version.
#
#   bash agent/guardrail/create.sh --dry-run   # print the config and the commands, call nothing
#   bash agent/guardrail/create.sh             # create or update in us-east-1, then publish a version
#
# Idempotent: it looks the guardrail up by name. If it already exists with a published
# version, a re-run changes nothing and prints the same id and version. Pass --update to
# push this file's config to the existing guardrail and publish a new version.
#
# Policies:
#   - PROMPT_ATTACK on input at HIGH (Standard tier, so Vietnamese and Spanish are covered).
#     The gateway calls ApplyGuardrail with source INPUT on untrusted tool output.
#   - PII: anonymize US_SOCIAL_SECURITY_NUMBER and PHONE. scan() lists PII hits but never
#     returns the masked text, and PII never counts as an attack.
# Prints GUARDRAIL_ID=... and GUARDRAIL_VERSION=... to paste into your env.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
NAME="${GUARDRAIL_NAME:-mandate-untrusted-input}"
PROFILE_ID="us.guardrail.v1:0"   # US cross-region guardrail profile, required by the Standard tier
DRY_RUN=false
UPDATE=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --update) UPDATE=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

CONTENT_POLICY='{"filtersConfig":[{"type":"PROMPT_ATTACK","inputStrength":"HIGH","outputStrength":"NONE"}],"tierConfig":{"tierName":"STANDARD"}}'
PII_POLICY='{"piiEntitiesConfig":[{"type":"US_SOCIAL_SECURITY_NUMBER","action":"ANONYMIZE"},{"type":"PHONE","action":"ANONYMIZE"}]}'
CROSS_REGION="{\"guardrailProfileIdentifier\":\"$PROFILE_ID\"}"
BLOCKED_MSG="Flagged by Mandate guardrail."
COMMON_ARGS=(
  --region "$REGION"
  --name "$NAME"
  --description "Mandate: flag prompt attacks in untrusted tool output (flag and log only)"
  --content-policy-config "$CONTENT_POLICY"
  --sensitive-information-policy-config "$PII_POLICY"
  --cross-region-config "$CROSS_REGION"
  --blocked-input-messaging "$BLOCKED_MSG"
  --blocked-outputs-messaging "$BLOCKED_MSG"
)

if $DRY_RUN; then
  echo "[dry-run] region: $REGION, name: $NAME"
  echo "[dry-run] content policy: $CONTENT_POLICY"
  echo "[dry-run] PII policy (ANONYMIZE): $PII_POLICY"
  echo "[dry-run] cross-region: $CROSS_REGION"
  echo "[dry-run] would run: aws bedrock list-guardrails --region $REGION   (find '$NAME')"
  echo "[dry-run] would run: aws bedrock create-guardrail ...   (skipped if it exists; --update runs update-guardrail)"
  echo "[dry-run] would run: aws bedrock create-guardrail-version --guardrail-identifier <id>   (only when created or updated)"
  echo "GUARDRAIL_ID=<dry-run>"
  echo "GUARDRAIL_VERSION=<dry-run>"
  exit 0
fi

ID=$(aws bedrock list-guardrails --region "$REGION" \
  --query "guardrails[?name=='$NAME'].id | [0]" --output text)

PUBLISH=false
if [[ "$ID" == "None" || -z "$ID" ]]; then
  echo "Creating guardrail $NAME ..." >&2
  ID=$(aws bedrock create-guardrail "${COMMON_ARGS[@]}" --query guardrailId --output text)
  PUBLISH=true
elif $UPDATE; then
  echo "Updating guardrail $NAME ($ID) ..." >&2
  aws bedrock update-guardrail "${COMMON_ARGS[@]}" --guardrail-identifier "$ID" >/dev/null
  PUBLISH=true
fi

# Highest published (numbered) version; versions are strings, so compare them as numbers.
LATEST=$(aws bedrock list-guardrails --region "$REGION" --guardrail-identifier "$ID" \
  --query "max_by(guardrails[?version!='DRAFT'], &to_number(version)).version" --output text)

if $PUBLISH || [[ "$LATEST" == "None" || -z "$LATEST" ]]; then
  # A new or updated guardrail is CREATING/UPDATING for a few seconds; publish once READY.
  for _ in $(seq 30); do
    STATUS=$(aws bedrock get-guardrail --region "$REGION" --guardrail-identifier "$ID" --query status --output text)
    [[ "$STATUS" == "READY" ]] && break
    [[ "$STATUS" == "FAILED" ]] && { echo "Guardrail $ID is FAILED; check the console." >&2; exit 1; }
    sleep 2
  done
  echo "Publishing a new version ..." >&2
  VERSION=$(aws bedrock create-guardrail-version --region "$REGION" --guardrail-identifier "$ID" \
    --description "mandate $(date -u +%Y-%m-%dT%H:%MZ)" --query version --output text)
else
  echo "Guardrail $NAME exists ($ID); reusing version $LATEST (pass --update to change it)." >&2
  VERSION="$LATEST"
fi

echo "GUARDRAIL_ID=$ID"
echo "GUARDRAIL_VERSION=$VERSION"
