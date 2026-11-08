#!/usr/bin/env bash
# Builds the Mandate Cognito user pool in us-east-1. Safe to run twice: each step looks the
# resource up first and creates it only if it is missing. Nothing is ever deleted.
#
#   bash Backend/infra/cognito.sh --dry-run     print every command, touch nothing (no aws needed)
#   bash Backend/infra/cognito.sh               really create it (needs aws, and the two passwords below)
#
# Real run needs MAYA_TEMP_PASSWORD and CHIEFOFSTAFF_TEMP_PASSWORD in the environment. Each must meet
# Cognito's default policy: 8+ characters with upper, lower, digit and symbol.
set -euo pipefail

REGION=us-east-1
POOL_NAME=mandate
DOMAIN_PREFIX=${COGNITO_DOMAIN_PREFIX:-mandate-a10-hack-2026}
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ENV_FILE=$REPO_ROOT/.env
DRY_RUN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --env-file)
      ENV_FILE=${2:?--env-file needs a path}
      # Absolute now, relative to where the caller stands, so a later cd cannot change the target.
      case "$ENV_FILE" in /*) ;; *) ENV_FILE=$PWD/$ENV_FILE ;; esac
      shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$DRY_RUN" = true ]; then
  MAYA_TEMP_PASSWORD=${MAYA_TEMP_PASSWORD:-'****'}
  CHIEFOFSTAFF_TEMP_PASSWORD=${CHIEFOFSTAFF_TEMP_PASSWORD:-'****'}
else
  # Fail before any change, so we never end up with a half-built pool.
  command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
  : "${MAYA_TEMP_PASSWORD:?set MAYA_TEMP_PASSWORD}"
  : "${CHIEFOFSTAFF_TEMP_PASSWORD:?set CHIEFOFSTAFF_TEMP_PASSWORD}"
  # The agent client secret goes into this file, so it must never be committed.
  git -C "$REPO_ROOT" check-ignore -q "$ENV_FILE" || { echo "$ENV_FILE is not git-ignored" >&2; exit 1; }
fi

# Prints the command (passwords shown as ****) on stderr; runs it only when not a dry run.
# The command's own stdout stays clean, so callers can capture an id with $(run ...).
run() {
  local shown="aws cognito-idp" arg
  for arg in "$@"; do
    if [ "$arg" = "$MAYA_TEMP_PASSWORD" ] || [ "$arg" = "$CHIEFOFSTAFF_TEMP_PASSWORD" ]; then
      arg='****'
    fi
    # Single-quote anything the shell would treat specially, so the line can be pasted back.
    case "$arg" in
      *[!A-Za-z0-9_./:=,@%+-]*) arg="'$arg'" ;;
    esac
    shown+=" $arg"
  done
  echo "$shown --region $REGION" >&2
  if [ "$DRY_RUN" = false ]; then
    aws cognito-idp "$@" --region "$REGION"
  fi
}

# Prints the value if the resource exists, nothing if it does not. A dry run never looks.
# The aws error is hidden because "not found" is an error for most describe/get calls; a wrong
# guess makes the create that follows fail loudly. List calls use list_lookup instead.
lookup() {
  [ "$DRY_RUN" = true ] && return 0
  local value
  value=$(aws cognito-idp "$@" --output text --region "$REGION" 2>/dev/null) || return 0
  [ "$value" = None ] || echo "$value"
}

# For list calls, where "nothing matches" prints None. Unlike lookup, an aws error is not "missing":
# it returns non-zero so set -e stops the script, with the aws error left visible on stderr.
list_lookup() {
  [ "$DRY_RUN" = true ] && return 0
  local value
  value=$(aws cognito-idp "$@" --output text --region "$REGION") || return
  [ "$value" = None ] || echo "$value"
}

step() { echo; echo "== $*"; }

create_group() {
  local group=$1
  step "Group $group"
  if [ -z "$(lookup get-group --user-pool-id "$POOL_ID" --group-name "$group" --query Group.GroupName)" ]; then
    run create-group --user-pool-id "$POOL_ID" --group-name "$group" > /dev/null
  fi
}

# The group is added every time: adding a user who is already a member changes nothing.
create_user() {
  local username=$1 password=$2 group=$3
  step "User $username in group $group"
  if [ -z "$(lookup admin-get-user --user-pool-id "$POOL_ID" --username "$username" --query Username)" ]; then
    # The temporary password is briefly visible in `ps` on this machine; fine, Cognito forces a change at first login.
    run admin-create-user --user-pool-id "$POOL_ID" --username "$username" \
      --temporary-password "$password" --message-action SUPPRESS > /dev/null
  fi
  run admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$username" --group-name "$group"
}

step "User pool $POOL_NAME (invite-only: no sign-up, MFA off, no self-service recovery)"
POOL_ID=$(list_lookup list-user-pools --max-results 60 --query "UserPools[?Name=='$POOL_NAME'].Id | [0]")
if [ -z "$POOL_ID" ]; then
  POOL_ID=$(run create-user-pool --pool-name "$POOL_NAME" \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --mfa-configuration OFF \
    --account-recovery-setting 'RecoveryMechanisms=[{Priority=1,Name=admin_only}]' \
    --username-configuration CaseSensitive=false \
    --query UserPool.Id --output text)
fi
POOL_ID=${POOL_ID:-<POOL_ID>}

step "Managed login domain $DOMAIN_PREFIX"
EXISTING_DOMAIN=$(lookup describe-user-pool --user-pool-id "$POOL_ID" --query UserPool.Domain)
if [ -z "$EXISTING_DOMAIN" ]; then
  run create-user-pool-domain --domain "$DOMAIN_PREFIX" --user-pool-id "$POOL_ID" --managed-login-version 2 > /dev/null
else
  DOMAIN_PREFIX=$EXISTING_DOMAIN  # a pool has one domain; keep the one it already has
fi

step "Resource server mandate with scope mandate/tools"
if [ -z "$(lookup describe-resource-server --user-pool-id "$POOL_ID" --identifier mandate --query ResourceServer.Identifier)" ]; then
  run create-resource-server --user-pool-id "$POOL_ID" --identifier mandate --name mandate \
    --scopes 'ScopeName=tools,ScopeDescription=Call Mandate MCP tools' > /dev/null
fi

# The agent client is built first. It has a secret and only the client_credentials flow.
step "App client mandate-agent (confidential, client_credentials, scope mandate/tools)"
AGENT_CLIENT_ID=$(list_lookup list-user-pool-clients --user-pool-id "$POOL_ID" --max-results 60 \
  --query "UserPoolClients[?ClientName=='mandate-agent'].ClientId | [0]")
if [ -z "$AGENT_CLIENT_ID" ]; then
  # --query keeps ClientSecret out of the output; it is fetched later and written only to the env file.
  AGENT_CLIENT_ID=$(run create-user-pool-client --user-pool-id "$POOL_ID" --client-name mandate-agent \
    --generate-secret \
    --allowed-o-auth-flows client_credentials --allowed-o-auth-flows-user-pool-client \
    --allowed-o-auth-scopes mandate/tools \
    --supported-identity-providers COGNITO \
    --query UserPoolClient.ClientId --output text)
fi
AGENT_CLIENT_ID=${AGENT_CLIENT_ID:-<AGENT_CLIENT_ID>}

# No secret makes this a public client, so the browser must use authorization code + PKCE.
# ALLOW_ADMIN_USER_PASSWORD_AUTH needs AWS credentials, so only a CLI fallback login can use it.
step "App client staff-web (public, authorization code + PKCE)"
STAFF_CLIENT_ID=$(list_lookup list-user-pool-clients --user-pool-id "$POOL_ID" --max-results 60 \
  --query "UserPoolClients[?ClientName=='staff-web'].ClientId | [0]")
if [ -z "$STAFF_CLIENT_ID" ]; then
  STAFF_CLIENT_ID=$(run create-user-pool-client --user-pool-id "$POOL_ID" --client-name staff-web \
    --no-generate-secret \
    --allowed-o-auth-flows code --allowed-o-auth-flows-user-pool-client \
    --allowed-o-auth-scopes openid email profile \
    --supported-identity-providers COGNITO \
    --callback-urls http://localhost:3000/ http://localhost:5173/ \
    --logout-urls http://localhost:3000/ http://localhost:5173/ \
    --prevent-user-existence-errors ENABLED \
    --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH ALLOW_ADMIN_USER_PASSWORD_AUTH \
    --query UserPoolClient.ClientId --output text)
fi
STAFF_CLIENT_ID=${STAFF_CLIENT_ID:-<STAFF_CLIENT_ID>}

step "Managed login page style for staff-web (the v2 login page errors without one)"
if [ -z "$(lookup describe-managed-login-branding-by-client --user-pool-id "$POOL_ID" --client-id "$STAFF_CLIENT_ID" \
  --query ManagedLoginBranding.ManagedLoginBrandingId)" ]; then
  run create-managed-login-branding --user-pool-id "$POOL_ID" --client-id "$STAFF_CLIENT_ID" \
    --use-cognito-provided-values > /dev/null
fi

create_group staffer
create_group admin
create_user maya "$MAYA_TEMP_PASSWORD" staffer
create_user chiefofstaff "$CHIEFOFSTAFF_TEMP_PASSWORD" admin

COGNITO_DOMAIN=https://$DOMAIN_PREFIX.auth.$REGION.amazoncognito.com

# Only public values: safe to print and to paste into the frontend config.
public_env() {
  echo "COGNITO_REGION=$REGION"
  echo "COGNITO_USER_POOL_ID=$POOL_ID"
  echo "COGNITO_STAFF_CLIENT_ID=$STAFF_CLIENT_ID"
  echo "COGNITO_AGENT_CLIENT_ID=$AGENT_CLIENT_ID"
  echo "COGNITO_DOMAIN=$COGNITO_DOMAIN"
}

if [ "$DRY_RUN" = false ]; then
  step "Writing $ENV_FILE"
  AGENT_CLIENT_SECRET=$(run describe-user-pool-client --user-pool-id "$POOL_ID" --client-id "$AGENT_CLIENT_ID" \
    --query UserPoolClient.ClientSecret --output text)
  # Build the new content in a private temp file first, so a failed read never truncates the .env.
  # grep exits 1 when no line is left to keep (fine); any other failure aborts the script.
  tmp_file=$(mktemp "$ENV_FILE.XXXXXX")
  { [ ! -f "$ENV_FILE" ] || grep -v '^COGNITO_' "$ENV_FILE" || [ $? -eq 1 ]; } > "$tmp_file"
  { public_env; echo "COGNITO_AGENT_CLIENT_SECRET=$AGENT_CLIENT_SECRET"; } >> "$tmp_file"
  if [ -f "$ENV_FILE" ]; then
    cat "$tmp_file" > "$ENV_FILE"  # write through the path so a symlinked .env stays a symlink
    rm "$tmp_file"
  else
    mv "$tmp_file" "$ENV_FILE"
  fi
  echo "secret written to $ENV_FILE"
fi

step "Backend env (public values)"
public_env

step "Frontend env for the Vite app (public values)"
echo "VITE_COGNITO_DOMAIN=$COGNITO_DOMAIN"
echo "VITE_COGNITO_CLIENT_ID=$STAFF_CLIENT_ID"
echo "VITE_COGNITO_REDIRECT_URI=http://localhost:5173/"
