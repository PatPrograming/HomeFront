#!/usr/bin/env bash
# Builds the KMS key that encrypts connected-account (Gmail, X) OAuth tokens, in us-east-1.
# Safe to run twice: the key is looked up by its alias and created only if missing. Nothing is
# ever deleted, and no IAM keys or policies are created.
#
#   bash Backend/infra/connections.sh             print every command, touch nothing (no aws needed)
#   bash Backend/infra/connections.sh --apply     really create it (needs aws and credentials)
#
# Dry run is the default, like every other HomeFront AWS script; only --apply changes AWS.
set -euo pipefail

REGION=us-east-1
ALIAS=alias/homefront-connections
DRY_RUN=true  # safe by default: nothing changes without --apply

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) DRY_RUN=false ;;
    --dry-run) ;;  # already the default; kept so old commands still work
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$DRY_RUN" = false ]; then
  # Fail before any change.
  command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
fi

# Prints the command on stderr; runs it only when not a dry run.
# The command's own stdout stays clean, so callers can capture a value with $(run ...).
run() {
  local shown="aws kms" arg
  for arg in "$@"; do
    # Single-quote anything the shell would treat specially, so the line can be pasted back.
    case "$arg" in
      *[!A-Za-z0-9_./:=,@%+-]*) arg="'$arg'" ;;
    esac
    shown+=" $arg"
  done
  echo "$shown --region $REGION" >&2
  if [ "$DRY_RUN" = false ]; then
    aws kms "$@" --region "$REGION"
  fi
}

# Prints the value if the key exists, nothing if it does not. A dry run never looks.
# The aws error is hidden because "not found" is an error for describe-key; a wrong guess makes
# the create-key that follows fail loudly or leaves the alias create to fail.
lookup() {
  [ "$DRY_RUN" = true ] && return 0
  aws kms "$@" --output text --region "$REGION" 2>/dev/null || return 0
}

step() { echo; echo "== $*"; }

step "KMS key $ALIAS (encrypts provider OAuth tokens)"
KEY_ID=$(lookup describe-key --key-id "$ALIAS" --query KeyMetadata.KeyId)
if [ -z "$KEY_ID" ]; then
  KEY_ID=$(run create-key --description "HomeFront connected-account tokens" \
    --key-usage ENCRYPT_DECRYPT --tags TagKey=project,TagValue=homefront \
    --query KeyMetadata.KeyId --output text)
  KEY_ID=${KEY_ID:-<KEY_ID>}
  run create-alias --alias-name "$ALIAS" --target-key-id "$KEY_ID"
else
  echo "reusing existing key $KEY_ID" >&2
fi
KEY_ID=${KEY_ID:-<KEY_ID>}

step "Yearly automatic key rotation"
run enable-key-rotation --key-id "$KEY_ID"

if [ "$DRY_RUN" = false ]; then
  KEY_ARN=$(aws kms describe-key --key-id "$ALIAS" --query KeyMetadata.Arn --output text --region "$REGION")
else
  KEY_ARN="arn:aws:kms:$REGION:<ACCOUNT_ID>:key/<KEY_ID>"
fi

step "Add this line to Backend/.env (not a secret)"
echo "CONNECTIONS_KMS_KEY_ID=$ALIAS"

step "IAM permission (example only, this script does not apply it)"
echo "The identity that runs the backend needs kms:Encrypt and kms:Decrypt on this key."
echo "Least-privilege policy: every call must send exactly the staffer and provider encryption context."
cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["kms:Encrypt", "kms:Decrypt"],
      "Resource": "$KEY_ARN",
      "Condition": {
        "Null": { "kms:EncryptionContextKeys": "false" },
        "ForAllValues:StringEquals": { "kms:EncryptionContextKeys": ["staffer", "provider"] }
      }
    }
  ]
}
POLICY
